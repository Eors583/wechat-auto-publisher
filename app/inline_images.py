from __future__ import annotations

import logging
import re
import textwrap
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
from lxml import html as lxml_html
from PIL import Image, ImageDraw, ImageFont

from app.ai.image_generator import generate_image
from app.ai.image_providers import is_image_provider
from app.ai.model_registry import decrypt_api_key
from app.db import Database
from app.wechat.material import batch_get_material, upload_article_image

logger = logging.getLogger(__name__)

_MARKDOWN_PREFIX = re.compile(r"^\s*(?:#{1,6}|>|[-*+] |\d+[.、])\s*")
_KEYWORD_RE = re.compile(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9-]{2,}|\d+(?:\.\d+)?%")
_SIGNALS = (
    (re.compile(r"案例|例如|比如|场景|实践|一家|企业|团队"), 5),
    (re.compile(r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?(?:万|亿|倍)|数据|增长|下降|成本|利润"), 5),
    (re.compile(r"方法|框架|步骤|策略|建议|行动|首先|其次|最后|关键"), 4),
    (re.compile(r"转向|与此同时|然而|但是|接下来|从.+到"), 2),
)
_STOP = {"一个", "一种", "这个", "这些", "我们", "他们", "以及", "通过", "进行", "可以", "不是", "需要"}
_HEADING = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")
_CLOSING_HEADING = re.compile(
    r"^(?:结语|结论|总结|全文总结|写在最后|最后的话)(?:[：:].*)?$"
)
_UNHELPFUL_SOURCE_IMAGE_MARKERS = (
    # 36Kr newsflashes expose this shared brand placeholder as og:image.
    "v2_1571894049839_img_jpg",
    "favicon",
    "placeholder",
    "default-cover",
    "default_cover",
    "site-logo",
    "site_logo",
    "/logo.",
    "/logo_",
    "/logo-",
    "qrcode",
    "qr-code",
)
_SCENE_HINTS = (
    (
        re.compile(r"影视|电影|电视剧|视频|摄制|文艺创作|文化产业"),
        "专业影视拍摄现场，摄影机、灯光、布景、导演和制作团队正在完成真实内容生产",
    ),
    (
        re.compile(r"资本市场|增持|回购|A股|股票|理财|资金|投资|股价"),
        "机构投资与资产配置现场，专业人员正在分析市场并执行真实投资决策，设备界面内容全部虚化",
    ),
    (
        re.compile(r"工厂|制造|产能|生产线|供应链|工业|仓储|物流"),
        "现代制造或供应链现场，真实设备、生产流程和工作人员共同呈现业务变化",
    ),
    (
        re.compile(r"人工智能|AI|数字化|互联网|软件|平台|算法|数据"),
        "真实数字化业务现场，专业人员使用现代设备完成产品研发或业务运营，屏幕内容不可辨认",
    ),
    (
        re.compile(r"零售|消费|门店|电商|品牌|用户|客户|产品"),
        "真实消费与产品服务场景，顾客、产品和业务人员之间存在明确的使用或交易关系",
    ),
    (
        re.compile(r"医疗|医药|医院|医生|患者|健康"),
        "真实医疗健康场景，专业人员、医疗设备和服务对象共同呈现论点中的具体变化",
    ),
    (
        re.compile(r"组织|管理|人才|招聘|团队|员工|协同"),
        "真实组织运营现场，团队成员通过具体工作流程协作解决论点所描述的问题",
    ),
)


@dataclass
class ImagePlan:
    index: int
    anchor: str
    offset: int
    keywords: list[str]
    caption: str
    prompt: str
    context: str = ""
    article_summary: str = ""
    primary_subject: str = ""
    argument_summary: str = ""


def _visual_concept(context: str, fallback: str) -> str:
    """Select one short conclusion instead of sending an article outline."""
    fragments = [
        item.strip()
        for item in re.split(r"[。！？；\n]+", context or "")
        if item.strip()
    ]
    label = re.compile(
        r"^(?:核心观点|关键结论|结论|结果|影响|行动建议|"
        r"原因(?:之一|之二|之三|一|二|三|\d+)?)\s*[：:]\s*"
    )

    def clean(value: str) -> str:
        value = label.sub("", value).strip("，。；： ")
        return re.sub(r"[“”\"《》【】]", "", value)

    for fragment in fragments:
        if label.match(fragment):
            result = clean(fragment)
            if result:
                return result[:120]
    for fragment in reversed(fragments):
        if re.search(r"因此|从而|意味着|体现|推动|提升|降低|实现|转向|重启", fragment):
            result = clean(fragment)
            if result:
                return result[:120]
    return clean(fallback)[:100]


def _scene_hint(context: str) -> str:
    hints = [hint for pattern, hint in _SCENE_HINTS if pattern.search(context)]
    if not hints:
        return "论点所描述行业中最具代表性的真实业务现场，主体正在执行关键行动并产生可见结果"
    return hints[0]


def _sanitize_visual_style(value: str) -> str:
    style = value.strip() or (
        "写实商业新闻纪实摄影风格，画面自然、克制、清晰，主体明确，"
        "保持整篇文章配图的色调和视觉语言一致"
    )
    style = re.sub(r"(?:商业)?杂志(?:摄影)?风格|(?:商业)?杂志风", "新闻纪实摄影风格", style)
    style = re.sub(r"海报风格|海报风", "写实摄影风格", style)
    return style


def plan_inline_images(
    body: str,
    *,
    min_count: int = 2,
    max_count: int = 6,
    min_spacing: int = 600,
    max_spacing: int = 900,
    placement_mode: str = "argument_end",
    prompt_style: str = "",
) -> list[ImagePlan]:
    """Place one image after each complete argument when headings are available."""
    paragraphs: list[dict[str, Any]] = []
    offset = 0
    section_id = 0
    section_title = ""
    section_level = 0
    for raw in re.split(r"\n\s*\n", body or ""):
        raw = raw.strip()
        if not raw:
            continue
        lines = raw.splitlines()
        heading = _HEADING.match(lines[0])
        if heading:
            section_id += 1
            section_level = len(heading.group(1))
            section_title = re.sub(r"[*_`]", "", heading.group(2)).strip()
            raw = "\n".join(lines[1:]).strip()
            if not raw:
                continue
        text = _MARKDOWN_PREFIX.sub("", raw).strip()
        text = re.sub(r"[*_`\[\]()#>]", "", text)
        if len(text) < 18:
            continue
        paragraphs.append(
            {
                "text": text,
                "offset": offset,
                "section_id": section_id,
                "section_title": section_title,
                "section_level": section_level,
            }
        )
        offset += len(text)
    if not paragraphs:
        return []

    desired = min(max_count, max(min_count, max(1, offset // max_spacing)))
    # Use the main repeated heading level as the article's argument structure.
    # For example, four ## headings with nested ### subpoints means four images.
    section_rows: dict[int, dict[str, Any]] = {}
    for paragraph in paragraphs:
        sid = int(paragraph["section_id"])
        if sid and sid not in section_rows:
            section_rows[sid] = paragraph
    level_counts: dict[int, int] = {}
    for paragraph in section_rows.values():
        title = str(paragraph.get("section_title") or "")
        if _CLOSING_HEADING.search(title):
            continue
        level = int(paragraph.get("section_level") or 0)
        level_counts[level] = level_counts.get(level, 0) + 1
    repeated_levels = sorted(level for level, count in level_counts.items() if count >= 2)
    primary_level = repeated_levels[0] if repeated_levels else min(level_counts, default=0)

    # A main argument includes all of its deeper subheadings. Its prompt therefore
    # receives the complete semantic block, and the image anchor is the final
    # paragraph before the next peer argument rather than the introductory text.
    argument_candidates: list[tuple[int, int, str, str, int]] = []
    seen_main_sections: set[int] = set()
    for start_index, paragraph in enumerate(paragraphs):
        sid = int(paragraph["section_id"])
        level = int(paragraph.get("section_level") or 0)
        title = str(paragraph.get("section_title") or "")
        if (
            not sid
            or sid in seen_main_sections
            or level != primary_level
            or _CLOSING_HEADING.search(title)
        ):
            continue
        seen_main_sections.add(sid)
        group: list[dict[str, Any]] = []
        for child in paragraphs[start_index:]:
            child_sid = int(child["section_id"])
            child_level = int(child.get("section_level") or 0)
            if child_sid != sid and child_level <= primary_level:
                break
            group.append(child)
        if not group:
            continue
        context_parts = [title]
        last_child_section = sid
        for child in group:
            child_sid = int(child["section_id"])
            child_title = str(child.get("section_title") or "")
            if child_sid != last_child_section and child_title:
                context_parts.append(child_title)
                last_child_section = child_sid
            context_parts.append(str(child["text"]))
        context = "。".join(part.strip("。") for part in context_parts if part).strip("。")
        last_paragraph = group[-1]
        signal_score = sum(weight for pattern, weight in _SIGNALS if pattern.search(context))
        argument_candidates.append(
            (
                14 + signal_score,
                int(last_paragraph["offset"]),
                str(last_paragraph["text"]),
                context,
                primary_level,
            )
        )

    candidates: list[tuple[int, int, str, str, int]] = list(argument_candidates)

    # Articles without enough clear argument blocks still get useful semantic anchors.
    if len(candidates) < desired:
        existing_offsets = {item[1] for item in candidates}
        for paragraph in paragraphs:
            pos = int(paragraph["offset"])
            if pos in existing_offsets:
                continue
            text = str(paragraph["text"])
            score = sum(weight for pattern, weight in _SIGNALS if pattern.search(text))
            if score:
                candidates.append((score, pos, text, text, 0))
    if len(candidates) < desired:
        existing_offsets = {item[1] for item in candidates}
        candidates.extend(
            (1, int(p["offset"]), str(p["text"]), str(p["text"]), 0)
            for p in paragraphs
            if int(p["offset"]) not in existing_offsets
        )

    argument_mode = placement_mode == "argument_end" and bool(argument_candidates)
    selected: list[tuple[int, str, str]] = []
    if argument_mode:
        # Clear titled arguments define the image count. min_count/max_count are
        # only fallbacks for articles whose structure has no reliable headings.
        selected = [
            (position, text, context)
            for _, position, text, context, _ in sorted(
                argument_candidates, key=lambda item: item[1]
            )
        ]
    else:
        # Without reliable argument boundaries, distribute a conservative number
        # of pictures according to article length and fallback settings.
        for slot in range(1, desired + 1):
            target = int(offset * slot / (desired + 1))
            viable = [
                c for c in candidates
                if all(abs(c[1] - p) >= min_spacing for p, _, _ in selected)
            ]
            if not viable:
                continue
            score, pos, text, context, _ = min(
                viable, key=lambda c: (abs(c[1] - target) - c[0] * 55, c[1])
            )
            selected.append((pos, text, context))
        selected.sort()

    plans: list[ImagePlan] = []
    planned_sections = selected if argument_mode else selected[:max_count]
    for index, (pos, text, context) in enumerate(planned_sections, 1):
        keywords = []
        for token in _KEYWORD_RE.findall(context):
            if token not in _STOP and token not in keywords:
                keywords.append(token)
            if len(keywords) >= 6:
                break
        caption = context[:46].rstrip("，。；：")
        argument_title = context.split("。", 1)[0].strip()
        visual_concept = _visual_concept(context, argument_title)
        scene_hint = _scene_hint(context)
        visual_style = _sanitize_visual_style(prompt_style)
        prompt = (
            "一张16:9横版写实新闻纪实照片，画面从边缘到边缘铺满，没有边框和留白。"
            f"{scene_hint}。"
            f"人物动作、真实环境和物体关系共同表达{visual_concept}。"
            "整张图只保留这一个连续场景，不做对比图、拼贴、分栏或图文版式。"
            f"{visual_style}。"
            "纯照片，所有屏幕、纸张和标牌内容均为不可辨认的虚化细节。"
        )
        plans.append(
            ImagePlan(
                index,
                text[:100],
                pos,
                keywords,
                caption,
                prompt,
                context=context[:1200],
            )
        )
    return plans


def _library_images(client: Any, limit: int = 200) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while offset < limit:
        page = batch_get_material(client, "image", offset, min(20, limit - offset))
        items = list(page.get("item") or [])
        rows.extend(items)
        offset += len(items)
        if not items or offset >= int(page.get("total_count") or 0):
            break
    return rows


def is_useful_source_image_url(url: str) -> bool:
    """Reject obvious publisher branding and shared placeholder images."""
    normalized = str(url or "").strip().casefold()
    if not normalized or not normalized.startswith(("http://", "https://")):
        return False
    return not any(marker in normalized for marker in _UNHELPFUL_SOURCE_IMAGE_MARKERS)


def _match_material(plan: ImagePlan, materials: list[dict[str, Any]], used: set[str]) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any]] | None = None
    for item in materials:
        media_id = str(item.get("media_id") or "")
        url = str(item.get("url") or "")
        if not media_id or not url or media_id in used:
            continue
        name = str(item.get("name") or "").casefold()
        score = sum(1 for keyword in plan.keywords if keyword.casefold() in name)
        if score and (best is None or score > best[0]):
            best = (score, item)
    return best[1] if best else None


def resolve_inline_images(
    *,
    body: str,
    settings: dict[str, Any],
    client: Any,
    db: Database,
    root: str | Path,
    job_id: int,
    source_images: list[str] | None = None,
    article_title: str = "",
    prompt_client: Any | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not settings.get("enabled", False):
        return [], []
    plans = plan_inline_images(
        body,
        min_count=int(settings.get("min_count", 2)),
        max_count=int(settings.get("max_count", 6)),
        min_spacing=int(settings.get("min_spacing", 600)),
        max_spacing=int(settings.get("max_spacing", 900)),
        placement_mode=str(settings.get("placement_mode") or "argument_end"),
        prompt_style=str(settings.get("prompt_style") or ""),
    )
    mode = str(settings.get("source_mode") or "generate")
    warnings: list[str] = []
    materials: list[dict[str, Any]] = []
    if mode in {"hybrid", "library"}:
        try:
            materials = _library_images(client)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"读取该公众号素材库失败：{exc}")

    image_model_id = str(settings.get("image_model_id") or "")
    model = db.get_ai_model(image_model_id) if image_model_id else None
    if model and not is_image_provider(model.get("provider_type")):
        model = None
    used: set[str] = set()
    assets: list[dict[str, Any]] = []
    if not plans:
        return [], ["没有识别到可插图的正文论点，请检查文章是否包含清晰的小标题和论述段落"]
    if (
        prompt_client is not None
        and mode in {"generate", "hybrid"}
        and model
        and bool(model.get("enabled"))
    ):
        from app.services.image_prompts import enrich_inline_image_prompts

        enriched, prompt_warnings = enrich_inline_image_prompts(
            article_title=article_title,
            body=body,
            plans=[asdict(plan) for plan in plans],
            client=prompt_client,
        )
        plans = [ImagePlan(**item) for item in enriched]
        warnings.extend(prompt_warnings)
    if mode == "generate":
        if not model or not bool(model.get("enabled")):
            return [], ["正文生图已启用，但所选生图智能体不存在或已停用"]
        return _generate_agent_images(
            plans=plans,
            model=model,
            client=client,
            root=root,
            job_id=job_id,
            concurrency=int(settings.get("generation_concurrency", 2)),
        )

    original_source_images = list(
        dict.fromkeys(str(url) for url in (source_images or []) if url)
    )
    source_queue = [url for url in original_source_images if is_useful_source_image_url(url)]
    ignored_source_count = len(original_source_images) - len(source_queue)
    if ignored_source_count:
        warnings.append(
            f"已忽略 {ignored_source_count} 张疑似媒体 Logo、默认封面或占位图"
        )
    for plan in plans:
        if source_queue and mode == "hybrid":
            source_url = source_queue.pop(0)
            try:
                target = Path(root) / "data" / "generated_images" / str(job_id) / f"source_{plan.index}.png"
                _download_article_image(source_url, target)
                url = upload_article_image(client, target)
                assets.append({**asdict(plan), "url": url, "source": "source", "media_id": ""})
                continue
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"原文第 {plan.index} 张图片下载或上传失败：{exc}")
        matched = _match_material(plan, materials, used) if mode != "generate" else None
        if matched:
            used.add(str(matched["media_id"]))
            assets.append({**asdict(plan), "url": str(matched["url"]), "source": "library", "media_id": str(matched["media_id"])})
            continue
        if mode == "library":
            continue
        if model and bool(model.get("enabled")) and mode != "library":
            try:
                target = (
                    Path(root)
                    / "data"
                    / "generated_images"
                    / str(job_id)
                    / f"inline_{plan.index}_{uuid.uuid4().hex[:8]}.jpg"
                )
                generate_image(
                    api_key=decrypt_api_key(str(model["api_key_encrypted"])),
                    api_base=str(model.get("api_base") or ""),
                    model=str(model.get("model") or ""),
                    provider_type=str(model.get("provider_type") or ""),
                    prompt=plan.prompt,
                    output_path=target,
                )
                url = upload_article_image(client, target)
                assets.append(
                    {
                        **asdict(plan),
                        "url": url,
                        "source": "generated",
                        "media_id": "",
                        "local_path": str(target),
                    }
                )
                continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("inline image generation failed")
                warnings.append(f"第 {plan.index} 张 AI 配图失败，已改用论点视觉卡片：{exc}")
        # Built-in content visualization guarantees useful images even before an
        # external image model is configured. It contains only article-derived text.
        try:
            target = Path(root) / "data" / "generated_images" / str(job_id) / f"card_{plan.index}.png"
            create_argument_card(plan, target)
            url = upload_article_image(client, target)
            assets.append({**asdict(plan), "url": url, "source": "visual_card", "media_id": ""})
        except Exception as exc:  # noqa: BLE001
            logger.exception("argument card creation failed")
            warnings.append(f"第 {plan.index} 张论点视觉卡片生成或上传失败：{exc}")
    return assets, list(dict.fromkeys(warnings))


def _generate_agent_images(
    *,
    plans: list[ImagePlan],
    model: dict[str, Any],
    client: Any,
    root: str | Path,
    job_id: int,
    concurrency: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Generate argument images concurrently, then upload to WeChat in order."""
    output_dir = Path(root) / "data" / "generated_images" / str(job_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    api_key = decrypt_api_key(str(model["api_key_encrypted"]))
    api_base = str(model.get("api_base") or "")
    model_name = str(model.get("model") or "")
    workers = max(1, min(4, int(concurrency or 2), len(plans)))
    generated: dict[int, Path] = {}
    warnings: list[str] = []

    def generate_one(plan: ImagePlan) -> tuple[int, Path]:
        target = output_dir / f"inline_{plan.index}_{uuid.uuid4().hex[:8]}.jpg"
        generate_image(
            api_key=api_key,
            api_base=api_base,
            model=model_name,
            provider_type=str(model.get("provider_type") or ""),
            prompt=plan.prompt,
            output_path=target,
        )
        return plan.index, target

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="argument-image") as pool:
        futures = {pool.submit(generate_one, plan): plan for plan in plans}
        for future in as_completed(futures):
            plan = futures[future]
            try:
                index, target = future.result()
                generated[index] = target
            except Exception as exc:  # noqa: BLE001
                logger.exception("argument image generation failed")
                warnings.append(f"论点 {plan.index} 生图失败：{exc}")

    assets: list[dict[str, Any]] = []
    for plan in plans:
        target = generated.get(plan.index)
        if target is None:
            continue
        try:
            url = upload_article_image(client, target)
            assets.append(
                {
                    **asdict(plan),
                    "url": url,
                    "source": "generated",
                    "media_id": "",
                    "local_path": str(target),
                    "model_id": str(model.get("id") or ""),
                    "model_name": str(model.get("name") or model_name),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("generated image upload failed")
            warnings.append(f"论点 {plan.index} 图片上传微信公众号失败：{exc}")
    return assets, list(dict.fromkeys(warnings))


def build_inline_image_revision_prompt(
    asset: dict[str, Any],
    instruction: str,
    *,
    article_title: str = "",
) -> str:
    """Build a one-image revision prompt with non-negotiable visual safeguards."""

    request = str(instruction or "").strip()
    if not request:
        raise ValueError("请先填写这张图片的修改要求")
    if len(request) > 2000:
        raise ValueError("单张图片修改要求不能超过 2000 字")
    base_prompt = str(asset.get("base_prompt") or asset.get("prompt") or "").strip()
    caption = str(asset.get("caption") or "当前论点").strip()
    anchor = str(asset.get("anchor") or "").strip()
    article_summary = str(asset.get("article_summary") or "").strip()
    primary_subject = str(asset.get("primary_subject") or "").strip()
    keywords = "、".join(str(item) for item in (asset.get("keywords") or []) if str(item).strip())
    return (
        "你正在重新生成微信公众号正文中的一张论点配图。\n"
        f"文章标题：{article_title or '未确定'}。\n"
        f"全文大意：{article_summary or '沿用原文章语境'}。\n"
        f"全文核心主体：{primary_subject or '沿用原配图主体'}。\n"
        f"当前论点：{caption}。\n"
        f"论点收束内容：{anchor or caption}。\n"
        f"语义关键词：{keywords or caption}。\n"
        f"原始视觉要求：{base_prompt or '使用写实新闻纪实摄影表达论点含义。'}\n"
        f"运营人员本次修改要求：{request}。\n"
        "请在不偏离当前论点事实和含义的前提下执行本次修改要求。"
        "只生成一张完整的16:9横版图片，只保留一个连续真实场景，主体和动作必须能表达论点。"
        "不得出现任何可读文字、标题、字幕、数字、字母、Logo、水印、二维码、边框、留白、"
        "海报、PPT、信息图、拼贴、分栏或网页界面；屏幕、纸张和标牌内容必须虚化且不可辨认。"
    )


def regenerate_inline_image_asset(
    *,
    asset: dict[str, Any],
    instruction: str,
    article_title: str,
    model: dict[str, Any],
    client: Any,
    root: str | Path,
    job_id: int,
) -> dict[str, Any]:
    """Generate and upload exactly one replacement while retaining its anchor."""

    if not model or not bool(model.get("enabled")) or not is_image_provider(
        model.get("provider_type")
    ):
        raise ValueError("该公众号绑定的生图智能体不存在、已停用或不是图片模型")
    image_index = int(asset.get("index") or asset.get("image_index") or 0)
    if image_index <= 0:
        raise ValueError("所选正文配图编号无效")
    prompt = build_inline_image_revision_prompt(
        asset,
        instruction,
        article_title=article_title,
    )
    output_dir = Path(root) / "data" / "generated_images" / str(job_id)
    target = output_dir / f"inline_{image_index}_revision_{uuid.uuid4().hex[:8]}.jpg"
    generate_image(
        api_key=decrypt_api_key(str(model["api_key_encrypted"])),
        api_base=str(model.get("api_base") or ""),
        model=str(model.get("model") or ""),
        provider_type=str(model.get("provider_type") or ""),
        prompt=prompt,
        output_path=target,
    )
    url = upload_article_image(client, target)
    updated = dict(asset)
    updated.update(
        {
            "index": image_index,
            "url": url,
            "source": "generated",
            "media_id": "",
            "base_prompt": str(asset.get("base_prompt") or asset.get("prompt") or ""),
            "last_revision_prompt": prompt,
            "revision_instruction": str(instruction).strip(),
            "revision_count": int(asset.get("revision_count") or 0) + 1,
            "local_path": str(target),
            "model_id": str(model.get("id") or ""),
            "model_name": str(model.get("name") or model.get("model") or "生图智能体"),
        }
    )
    return updated


def _download_article_image(url: str, output_path: str | Path) -> Path:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; WeChatAutoPublisher/0.1)"}
    with httpx.Client(follow_redirects=True, timeout=45, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        if len(response.content) > 12 * 1024 * 1024:
            raise ValueError("图片超过 12MB")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    from io import BytesIO

    with Image.open(BytesIO(response.content)) as image:
        image.convert("RGB").save(target, format="PNG", optimize=True)
    return target


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def create_argument_card(plan: ImagePlan, output_path: str | Path) -> Path:
    """Create a restrained, article-derived visual card without an external image API."""
    width, height = 1200, 800
    palettes = [
        ((13, 50, 74), (22, 118, 139), (80, 210, 190)),
        ((38, 33, 75), (95, 62, 160), (245, 166, 35)),
        ((54, 43, 32), (142, 90, 52), (229, 181, 103)),
        ((24, 55, 40), (46, 125, 82), (139, 213, 143)),
    ]
    start, end, accent = palettes[(plan.index - 1) % len(palettes)]
    image = Image.new("RGB", (width, height), start)
    pixels = image.load()
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(int(start[i] * (1 - ratio) + end[i] * ratio) for i in range(3))
        for x in range(width):
            pixels[x, y] = color
    draw = ImageDraw.Draw(image, "RGBA")
    draw.ellipse((820, -150, 1320, 350), fill=(*accent, 38))
    draw.ellipse((-180, 570, 310, 1060), fill=(255, 255, 255, 18))
    draw.rounded_rectangle((72, 62, 1128, 738), radius=28, fill=(255, 255, 255, 232))
    draw.rounded_rectangle((112, 112, 130, 278), radius=9, fill=(*accent, 255))
    draw.text((164, 112), f"核心论点  {plan.index:02d}", font=_font(28, bold=True), fill=(*accent, 255))

    title = plan.caption.strip() or "文章核心论点"
    title_lines = textwrap.wrap(title, width=18)[:3]
    y = 178
    for line in title_lines:
        draw.text((164, y), line, font=_font(48, bold=True), fill=(30, 38, 43, 255))
        y += 72
    draw.line((164, y + 16, 1036, y + 16), fill=(30, 38, 43, 30), width=2)

    keyword_y = y + 62
    x = 164
    for keyword in plan.keywords[:5]:
        label = str(keyword)[:10]
        box_width = max(116, 34 * len(label) + 48)
        if x + box_width > 1036:
            keyword_y += 72
            x = 164
        draw.rounded_rectangle(
            (x, keyword_y, x + box_width, keyword_y + 48),
            radius=24,
            fill=(*accent, 28),
            outline=(*accent, 110),
            width=2,
        )
        draw.text((x + 24, keyword_y + 8), label, font=_font(23), fill=(45, 56, 61, 255))
        x += box_width + 18
    draw.text((164, 668), "ARGUMENT · INSIGHT · ACTION", font=_font(18), fill=(70, 82, 87, 150))
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return target


def insert_inline_images(generated_html: str, assets: list[dict[str, Any]]) -> str:
    if not assets:
        return generated_html
    root = lxml_html.fragment_fromstring(generated_html or "", create_parent="div")
    paragraphs = root.xpath(".//p")
    last_position = -2
    for asset in assets:
        anchor = re.sub(r"\s+", "", str(asset.get("anchor") or ""))[:50]
        target = None
        position = -1
        for idx, paragraph in enumerate(paragraphs):
            text = re.sub(r"\s+", "", "".join(paragraph.itertext()))
            if anchor and (anchor in text or text[:30] in anchor):
                target, position = paragraph, idx
                break
        if target is None or position <= last_position:
            continue
        figure = lxml_html.Element("section")
        figure.set("data-inline-image-id", str(asset.get("index") or ""))
        figure.set("style", "margin:24px 0;text-align:center;line-height:1.6")
        image = lxml_html.Element("img")
        image.set("src", str(asset.get("url") or ""))
        image.set("alt", str(asset.get("caption") or "文章配图"))
        image.set("style", "display:block;width:100%;max-width:100%;height:auto;border-radius:4px")
        figure.append(image)
        caption = lxml_html.Element("p")
        caption.set("style", "margin:8px 0 0;color:#999;font-size:12px;text-align:center;line-height:1.6")
        caption.text = str(asset.get("caption") or "")
        figure.append(caption)
        target.addnext(figure)
        last_position = position
    return "".join(lxml_html.tostring(child, encoding="unicode", method="html") for child in root)


def remove_inline_image(article_html: str, image_index: int) -> str:
    root = lxml_html.fragment_fromstring(article_html or "", create_parent="div")
    for node in root.xpath(f'.//*[@data-inline-image-id="{int(image_index)}"]'):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    return "".join(lxml_html.tostring(child, encoding="unicode", method="html") for child in root)


def invalidate_inline_image_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Invalidate generated pictures after article text changes."""
    result = dict(meta or {})
    result["inline_images_resolved"] = False
    result["inline_images"] = []
    result["inline_image_warnings"] = []
    return result
