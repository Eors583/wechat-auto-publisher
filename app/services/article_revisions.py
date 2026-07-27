from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any


_FENCE = re.compile(r"^```(?:markdown|md|text)?\s*([\s\S]*?)\s*```$", re.I)
_ANSWER_PREFIX = re.compile(r"^(?:修改后(?:的段落)?|改写后(?:的段落)?|新段落)\s*[：:]\s*")
_MARKDOWN_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.、]\s+)")


@dataclass(frozen=True, slots=True)
class ParagraphRevision:
    original: str
    replacement: str
    body: str


def split_paragraphs(body: str) -> list[str]:
    """Split the editor's plain-text article into stable review blocks."""

    return [
        item.strip()
        for item in re.split(r"\n\s*\n", str(body or "").replace("\r\n", "\n"))
        if item.strip()
    ]


def revise_paragraph(
    client: Any,
    *,
    body: str,
    paragraph_index: int,
    instruction: str,
    title: str = "",
    topic: str = "",
    article_instruction: str = "",
) -> ParagraphRevision:
    """Ask the account's text model to revise exactly one review block."""

    paragraphs = split_paragraphs(body)
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        raise ValueError("所选段落不存在")
    request = str(instruction or "").strip()
    if not request:
        raise ValueError("请先填写这段正文的修改要求")
    if len(request) > 2000:
        raise ValueError("单次修改要求不能超过 2000 字")

    original = paragraphs[paragraph_index]
    previous = paragraphs[paragraph_index - 1] if paragraph_index > 0 else "（这是第一段）"
    following = (
        paragraphs[paragraph_index + 1]
        if paragraph_index + 1 < len(paragraphs)
        else "（这是最后一段）"
    )
    structural_rule = _structural_rule(original)
    account_rules = str(article_instruction or "").strip()[:6000]
    prompt = f"""你正在对一篇微信公众号文章做定向二次修改。

【文章信息】
标题：{title or '未确定'}
话题：{topic or '未提供'}

【上一个段落，仅用于衔接，不得改写】
{previous}

【需要修改的原段落】
{original}

【下一个段落，仅用于衔接，不得改写】
{following}

【运营人员的修改要求】
{request}

【该公众号的写作要求】
{account_rules or '延续当前文章的写作风格、语气和表达习惯。'}
其中涉及 JSON、标题候选、整篇字数或一次输出整篇文章的协议不在本阶段执行；本阶段只修改目标段落。

【硬性要求】
1. 只输出修改后的目标段落，不输出解释、前后文、候选版本或 Markdown 代码块。
2. 保留原文事实、专有名词、关键数字和因果关系，不得虚构材料中没有的信息。
3. 与前后段自然衔接，不重复前后文，不改变文章其他段落。
4. {structural_rule}
"""
    replacement = _clean_model_replacement(str(client.complete(prompt) or ""), original)
    if not replacement:
        raise RuntimeError("模型没有返回修改后的段落")
    paragraphs[paragraph_index] = replacement
    return ParagraphRevision(
        original=original,
        replacement=replacement,
        body="\n\n".join(paragraphs),
    )


def preserve_inline_images_after_paragraph_revision(
    meta: dict[str, Any] | None,
    *,
    original: str,
    replacement: str,
) -> dict[str, Any]:
    """Keep reviewed images and move an affected anchor to the replacement text.

    A single paragraph revision must not silently regenerate every image. If that
    paragraph is the anchor of one argument image, update only its anchor so the
    existing image remains at the same semantic position after rerendering.
    """

    result = copy.deepcopy(dict(meta or {}))
    assets = list(result.get("inline_images") or [])
    old_text = _anchor_text(original)
    new_text = _anchor_text(replacement)
    for asset in assets:
        anchor = _anchor_text(str(asset.get("anchor") or ""))
        if anchor and old_text and (anchor in old_text or old_text in anchor):
            asset["anchor"] = new_text[:100]
    result["inline_images"] = assets
    if assets:
        result["inline_images_resolved"] = True
    return result


def append_revision_event(
    meta: dict[str, Any] | None,
    *,
    kind: str,
    instruction: str,
    target: int,
) -> dict[str, Any]:
    """Store a compact audit trail without exposing credentials or model output."""

    result = copy.deepcopy(dict(meta or {}))
    events = list(result.get("revision_events") or [])
    events.append(
        {
            "kind": str(kind),
            "target": int(target),
            "instruction": str(instruction or "").strip()[:500],
        }
    )
    result["revision_events"] = events[-50:]
    return result


def _structural_rule(original: str) -> str:
    stripped = original.lstrip()
    heading = re.match(r"(#{1,6})\s+", stripped)
    if heading:
        return f"这是 {len(heading.group(1))} 级 Markdown 小标题，输出时必须保留相同数量的 # 前缀。"
    if _MARKDOWN_PREFIX.match(stripped):
        return "保留原段落的引用或列表标记及其语义层级。"
    return "输出一个正文段落；只在确有必要时保留原有的少量加粗标记。"


def _clean_model_replacement(value: str, original: str) -> str:
    text = str(value or "").strip()
    fence = _FENCE.match(text)
    if fence:
        text = fence.group(1).strip()
    text = _ANSWER_PREFIX.sub("", text, count=1).strip()
    if not text:
        return ""

    original_heading = re.match(r"^\s*(#{1,6})\s+", original)
    if original_heading:
        level = original_heading.group(1)
        text = re.sub(r"^\s*#{1,6}\s+", "", text).strip()
        text = re.split(r"\n\s*\n", text, maxsplit=1)[0].strip()
        return f"{level} {text}" if text else ""

    blocks = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    if len(blocks) > 1:
        text = " ".join(blocks)
    return text.strip()


def _anchor_text(value: str) -> str:
    value = _MARKDOWN_PREFIX.sub("", str(value or "").strip())
    value = re.sub(r"[*_`\[\]()#>]", "", value)
    return re.sub(r"\s+", "", value)
