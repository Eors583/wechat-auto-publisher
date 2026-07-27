from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.ai.image_generator import generate_image
from app.ai.image_providers import is_image_provider
from app.ai.model_registry import decrypt_api_key
from app.db import Database
from app.wechat.material import upload_permanent_image


_MARKDOWN = re.compile(r"(?:^|\n)\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.、]\s+)")
_INLINE_MARKDOWN = re.compile(r"[*_`\[\]{}<>]")
_SPACE = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[。！？；])")
_CLOSING = re.compile(r"^(?:结语|结论|总结|写在最后|最后的话)$")


@dataclass(frozen=True)
class GeneratedCover:
    media_id: str
    url: str
    local_path: str
    prompt: str
    model_id: str
    model_name: str
    source: str = "generated"


def build_cover_prompt(
    *,
    title: str,
    body: str,
    prompt_style: str = "",
    instruction: str = "",
) -> str:
    """Create a compact semantic brief for a text-free article cover.

    The title and body are both supplied to the model, but only as meaning. The
    prompt explicitly forbids rendering them as typography because article-cover
    models otherwise tend to turn a title into a poster.
    """
    clean_title = _clean_text(title)[:80] or "本篇文章的核心主题"
    headings = _headings(body)[:3]
    summary = _summary(body, headings=headings)
    focus = "；".join(headings) if headings else summary
    style = _sanitize_style(prompt_style)
    revision = _clean_text(instruction)[:1000]
    revision_rule = (
        f"运营人员对本次封面的具体要求是：{revision}。" if revision else ""
    )
    return (
        "生成一张微信公众号文章封面主图，2.35:1超宽横版构图，写实新闻纪实摄影。"
        "画面从边缘到边缘铺满，只保留一个连续、具体、真实的场景，主体明确，视觉焦点位于画面中央区域。"
        f"文章标题的含义是：{clean_title}。"
        f"正文核心内容是：{summary}。"
        f"需要重点表达的核心论点是：{focus}。"
        f"{revision_rule}"
        "请用人物行动、真实环境、关键物体和空间关系表达上述含义，不要把标题或正文排版到图片里。"
        f"视觉风格：{style}。"
        "纯照片；不得出现任何可读文字、标题、字幕、数字、字母、Logo、水印、二维码、边框、留白、"
        "海报、PPT、信息图、杂志版式、拼贴、分栏或网页界面；屏幕、纸张、标牌内容必须虚化且不可辨认。"
    )


def generate_article_cover(
    *,
    title: str,
    body: str,
    settings: dict[str, Any],
    db: Database,
    client: Any,
    root: str | Path,
    job_id: int,
    instruction: str = "",
) -> dict[str, str]:
    """Generate an article-aware cover and upload it as permanent WeChat media."""
    model_id = str(settings.get("image_model_id") or "").strip()
    model = db.get_ai_model(model_id) if model_id else None
    if (
        not model
        or not bool(model.get("enabled"))
        or not is_image_provider(model.get("provider_type"))
    ):
        raise ValueError("所选生图智能体不存在、已停用或不是图片模型")

    prompt = build_cover_prompt(
        title=title,
        body=body,
        prompt_style=str(settings.get("prompt_style") or ""),
        instruction=instruction,
    )
    target = (
        Path(root)
        / "data"
        / "generated_images"
        / str(job_id)
        / f"cover_{uuid.uuid4().hex[:8]}.jpg"
    )
    generate_image(
        api_key=decrypt_api_key(str(model["api_key_encrypted"])),
        api_base=str(model.get("api_base") or ""),
        model=str(model.get("model") or ""),
        provider_type=str(model.get("provider_type") or ""),
        prompt=prompt,
        output_path=target,
    )
    _crop_wechat_cover(target)
    uploaded = upload_permanent_image(client, target)
    result = GeneratedCover(
        media_id=str(uploaded["media_id"]),
        url=str(uploaded.get("url") or ""),
        local_path=str(target),
        prompt=prompt,
        model_id=str(model.get("id") or ""),
        model_name=str(model.get("name") or model.get("model") or "生图智能体"),
    )
    return asdict(result)


def invalidate_generated_cover(
    meta: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Remove only an active AI cover, preserving an operator-selected cover."""
    result = dict(meta or {})
    active = bool(result.get("generated_cover_active"))
    if active:
        result.pop("generated_cover", None)
        result.pop("cover_image_warning", None)
        result["generated_cover_active"] = False
    return result, active


def _headings(body: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*$", body or ""):
        heading = _clean_text(match.group(1))
        if heading and not _CLOSING.fullmatch(heading) and heading not in values:
            values.append(heading[:50])
    return values


def _summary(body: str, *, headings: list[str]) -> str:
    clean = _clean_text(_MARKDOWN.sub("\n", body or ""))
    sentences = [
        item.strip(" ，。；")
        for item in _SENTENCE.split(clean)
        if len(item.strip()) >= 12
    ]
    selected: list[str] = []
    for sentence in sentences:
        if any(token in sentence for token in ("因此", "意味着", "核心", "关键", "推动", "增长", "转型")):
            selected.append(sentence)
        if len(selected) >= 2:
            break
    if not selected:
        selected = sentences[:2]
    summary = "".join(selected)[:260].strip(" ，。；")
    if summary:
        return summary
    if headings:
        return "；".join(headings)
    return "文章围绕标题主题展开分析，并给出核心判断和行动启示"


def _clean_text(value: str) -> str:
    value = _INLINE_MARKDOWN.sub("", str(value or ""))
    return _SPACE.sub(" ", value).strip()


def _sanitize_style(value: str) -> str:
    style = _clean_text(value) or "真实、克制、自然、清晰的商业新闻摄影，色调统一"
    style = re.sub(r"(?:商业)?杂志(?:摄影)?风格|(?:商业)?杂志风", "新闻纪实摄影风格", style)
    style = re.sub(r"海报风格|海报风", "写实摄影风格", style)
    return style[:600]


def _crop_wechat_cover(path: Path) -> None:
    """Center-crop model output to the common 2.35:1 WeChat cover ratio."""
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        width, height = image.size
        target_ratio = 2.35
        if width / max(height, 1) > target_ratio:
            crop_width = int(height * target_ratio)
            left = max(0, (width - crop_width) // 2)
            image = image.crop((left, 0, left + crop_width, height))
        else:
            crop_height = int(width / target_ratio)
            top = max(0, (height - crop_height) // 2)
            image = image.crop((0, top, width, top + crop_height))
        image = image.resize((1410, 600), Image.Resampling.LANCZOS)
        image.save(path, format="JPEG", quality=86, optimize=True)
