from __future__ import annotations

import json
import re
from typing import Any

_NO_TEXT_SAFEGUARD = (
    "只生成一张16:9横版、边缘到边缘铺满的完整画面。"
    "画面不得出现任何可读的大段文字、标题、字幕、数字、字母、Logo、水印、二维码，"
    "不得做成海报、PPT、信息图、拼贴、分栏或网页界面；屏幕、纸张和标牌必须虚化。"
)


def enrich_inline_image_prompts(
    *,
    article_title: str,
    body: str,
    plans: list[dict[str, Any]],
    client: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Use the article's text agent to align all generated images to one subject."""

    if not plans or client is None:
        return plans, []
    try:
        brief = _article_visual_brief(client, article_title, body)
        suggestions = _argument_image_prompts(client, brief, plans)
    except Exception as exc:  # noqa: BLE001
        return plans, [f"图文提示词智能体处理失败，已使用内置提示词：{exc}"]

    warnings: list[str] = []
    missing = [
        int(plan.get("index") or 0)
        for plan in plans
        if int(plan.get("index") or 0) not in suggestions
    ]
    if missing:
        warnings.append(
            f"图文提示词智能体漏掉配图 {missing}，这些图片已使用内置提示词"
        )
    enriched: list[dict[str, Any]] = []
    for plan in plans:
        item = dict(plan)
        suggestion = suggestions.get(int(item.get("index") or 0), {})
        generated_prompt = str(suggestion.get("prompt") or "").strip()
        if generated_prompt:
            item["prompt"] = _with_safeguards(
                f"核心主体必须围绕{brief['primary_subject']}；"
                f"统一视觉方向：{brief['subject_visual_direction']}。"
                f"{generated_prompt}"
            )
        item["article_summary"] = brief["article_summary"]
        item["primary_subject"] = brief["primary_subject"]
        item["argument_summary"] = str(
            suggestion.get("argument_summary") or item.get("caption") or ""
        ).strip()[:300]
        enriched.append(item)
    return enriched, warnings


def _article_visual_brief(
    client: Any,
    article_title: str,
    body: str,
) -> dict[str, str]:
    prompt = (
        "你是微信公众号文章的视觉总编。先总结文章大意，再识别全文持续描述的核心主体。"
        "主体优先是明确出现的企业、品牌、人物、产品或组织；例如文章核心是华为，后续所有配图"
        "都应围绕华为的业务、技术或组织语境，而不是泛化成无关企业。不要编造文章没有提供的事实。\n"
        "只返回 JSON："
        '{"article_summary":"不超过180字","primary_subject":"核心主体名称或类别",'
        '"subject_visual_direction":"后续配图保持主体一致的视觉方向，不超过180字"}。\n'
        f"文章标题：{str(article_title or '未确定')[:160]}\n"
        f"文章内容：\n{_representative_text(body, 3200)}"
    )
    data = _complete_json(
        client,
        prompt,
        {
            "type": "object",
            "properties": {
                "article_summary": {"type": "string"},
                "primary_subject": {"type": "string"},
                "subject_visual_direction": {"type": "string"},
            },
            "required": [
                "article_summary",
                "primary_subject",
                "subject_visual_direction",
            ],
            "additionalProperties": False,
        },
        title="文章视觉主体分析",
    )
    summary = str(data.get("article_summary") or "").strip()[:300]
    subject = str(data.get("primary_subject") or "").strip()[:120]
    direction = str(data.get("subject_visual_direction") or "").strip()[:300]
    if not summary or not subject or not direction:
        raise ValueError("没有返回完整的文章摘要、核心主体和视觉方向")
    return {
        "article_summary": summary,
        "primary_subject": subject,
        "subject_visual_direction": direction,
    }


def _argument_image_prompts(
    client: Any,
    brief: dict[str, str],
    plans: list[dict[str, Any]],
) -> dict[int, dict[str, str]]:
    plan_count = max(1, len(plans))
    context_limit = max(80, min(500, 1800 // plan_count))
    direction_limit = max(40, min(180, 500 // plan_count))
    targets = [
        {
            "index": int(plan.get("index") or 0),
            "target_paragraph": str(
                plan.get("context") or plan.get("anchor") or plan.get("caption") or ""
            )[:context_limit],
            "existing_visual_direction": str(plan.get("prompt") or "")[
                :direction_limit
            ],
        }
        for plan in plans
    ]
    prompt = (
        "你是微信公众号的图文内容提示词智能体。根据全文摘要和核心主体，逐项分析目标论点或段落大意，"
        "再为图像生成模型编写可直接使用的中文提示词。所有图片必须围绕同一个核心主体，但每张图的"
        "场景和动作要准确表达对应论点；段落未直接提及主体时，要做自然的业务语境关联，不能虚构事实。\n"
        "每张图使用一个连续、真实、可拍摄的场景，明确主体、环境、动作、物体关系、构图、光线和风格。"
        "不要生成含大量文字的图片；不要海报、PPT、信息图、拼贴、分栏或网页界面。\n"
        "只返回 JSON："
        '{"images":[{"index":1,"argument_summary":"该论点大意，不超过120字",'
        '"prompt":"给图像模型的完整提示词"}]}。必须覆盖全部 index。\n'
        f"全文摘要：{brief['article_summary']}\n"
        f"核心主体：{brief['primary_subject']}\n"
        f"统一视觉方向：{brief['subject_visual_direction']}\n"
        f"目标论点：{json.dumps(targets, ensure_ascii=False)}"
    )
    data = _complete_json(
        client,
        prompt,
        {
            "type": "object",
            "properties": {
                "images": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {"type": "integer"},
                            "argument_summary": {"type": "string"},
                            "prompt": {"type": "string"},
                        },
                        "required": ["index", "argument_summary", "prompt"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["images"],
            "additionalProperties": False,
        },
        title="正文配图提示词生成",
    )
    return {
        int(item.get("index") or 0): {
            "argument_summary": str(item.get("argument_summary") or "").strip(),
            "prompt": str(item.get("prompt") or "").strip(),
        }
        for item in list(data.get("images") or [])
        if isinstance(item, dict) and int(item.get("index") or 0) > 0
    }


def _complete_json(
    client: Any,
    prompt: str,
    schema: dict[str, Any],
    *,
    title: str,
) -> dict[str, Any]:
    native = getattr(client, "complete_json", None)
    if callable(native):
        value = native(prompt, schema, title=title)
        if isinstance(value, dict):
            return value
    raw = str(client.complete(prompt) or "").strip()
    raw = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("没有返回 JSON")
    value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("返回结果不是 JSON 对象")
    return value


def _representative_text(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    part = limit // 3
    middle = len(value) // 2
    return (
        value[:part]
        + "\n……（中间内容均匀抽取）……\n"
        + value[middle - part // 2 : middle + part // 2]
        + "\n……（结尾）……\n"
        + value[-part:]
    )[:limit]


def _with_safeguards(prompt: str) -> str:
    return f"{str(prompt).strip()[:1600]}。{_NO_TEXT_SAFEGUARD}"
