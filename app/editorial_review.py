from __future__ import annotations

from typing import Any


STRICTNESS_LEVELS: dict[str, dict[str, str]] = {
    "lenient": {
        "name": "宽松",
        "description": "只指出会明显影响点击、完读、互动或发布安全的问题。",
    },
    "standard": {
        "name": "标准",
        "description": "兼顾传播效果、修改成本和发布效率。",
    },
    "strict": {
        "name": "严格",
        "description": "严格评估传播效果、事实和合规风险，但不逐段挑字眼。",
    },
}


ENGAGEMENT_REVIEW_DIMENSIONS: tuple[dict[str, str], ...] = (
    {
        "id": "title_click",
        "name": "标题点击力",
        "description": "标题是否准确、有吸引力，并与正文承诺一致。",
    },
    {
        "id": "opening_retention",
        "name": "开头留存力",
        "description": "开头能否迅速建立阅读理由，让读者愿意继续。",
    },
    {
        "id": "completion_potential",
        "name": "完读潜力",
        "description": "结构、节奏和信息密度是否支持读者读到最后。",
    },
    {
        "id": "like_potential",
        "name": "点赞潜力",
        "description": "文章是否提供认同感、获得感或值得肯定的观点。",
    },
    {
        "id": "share_potential",
        "name": "转发潜力",
        "description": "文章是否具有分享价值、社交表达价值或实用价值。",
    },
)


REVIEW_ROLES: dict[str, dict[str, Any]] = {
    "chief_editor": {
        "name": "主编",
        "icon": "edit_note",
        "description": "从整篇判断结构、核心观点和传播完成度，不逐段润色。",
        "dimensions": ["标题点击力", "开头留存力", "完读潜力"],
        "default_scope": "article",
        "may_rewrite": True,
    },
    "target_reader": {
        "name": "目标读者",
        "icon": "person_search",
        "description": "判断读者是否愿意点开、继续阅读、读完并表达认同。",
        "dimensions": ["开头留存力", "完读潜力", "点赞潜力"],
        "default_scope": "article",
        "may_rewrite": True,
    },
    "title_expert": {
        "name": "标题专家",
        "icon": "title",
        "description": "检查点击力、准确性以及标题与正文是否匹配。",
        "dimensions": ["标题点击力", "标题准确性", "标题正文匹配"],
        "default_scope": "title",
        "may_rewrite": True,
    },
    "fact_checker": {
        "name": "事实核查",
        "icon": "fact_check",
        "description": "标记数据、时间、人物、机构、来源和因果关系风险。",
        "dimensions": ["事实", "数据", "时间", "人物与来源"],
        "default_scope": "advice_only",
        "may_rewrite": False,
        "facts_only": True,
        "can_block_draft": True,
    },
    "compliance_expert": {
        "name": "合规专家",
        "icon": "gavel",
        "description": "检查敏感表达、广告法、侵权和不当承诺风险。",
        "dimensions": ["合规", "广告法", "侵权", "敏感表达"],
        "default_scope": "advice_only",
        "may_rewrite": False,
        "can_block_draft": True,
    },
    "brand_advisor": {
        "name": "品牌顾问",
        "icon": "verified",
        "description": "判断整篇是否符合公众号定位、账号人设和品牌边界。",
        "dimensions": ["品牌定位", "点赞潜力", "转发潜力"],
        "default_scope": "article",
        "may_rewrite": True,
    },
    "growth_operator": {
        "name": "增长运营",
        "icon": "trending_up",
        "description": "判断完读、点赞和转发动机，以及文章的分享价值。",
        "dimensions": ["完读潜力", "点赞潜力", "转发潜力"],
        "default_scope": "article",
        "may_rewrite": True,
    },
}


REVIEW_STYLES: dict[str, dict[str, str]] = {
    "rigorous": {"name": "严谨专业", "description": "结论有依据，表达专业准确。"},
    "humorous": {"name": "风趣幽默", "description": "适度轻松，但不拿严肃事实开玩笑。"},
    "sharp": {"name": "犀利观点", "description": "观点鲜明直接，但不夸大或制造冲突。"},
    "accessible": {"name": "通俗易懂", "description": "降低理解门槛，避免不必要术语。"},
    "empathetic": {"name": "温暖共情", "description": "理解读者处境，避免居高临下。"},
    "deep_analysis": {"name": "深度分析", "description": "强化因果、方法论和可迁移启示。"},
    "concise": {"name": "简洁克制", "description": "提高信息密度，减少铺垫、口号和重复。"},
    "storytelling": {"name": "故事化表达", "description": "通过场景和叙事增强理解，不虚构事实。"},
}


BUILTIN_REVIEW_SCHEMES: dict[str, dict[str, Any]] = {
    "professional_depth": {
        "id": "professional_depth",
        "name": "专业深度型",
        "description": "适合行业分析、企业管理和财经文章。",
        "role_ids": ["chief_editor", "fact_checker", "brand_advisor"],
        "style_ids": ["rigorous", "deep_analysis"],
        "strictness": "standard",
        "focus": "强化观点深度和企业经营价值；重点优化标题、开头、完读、点赞和转发动机，不逐段挑字眼。",
    },
    "viral_growth": {
        "id": "viral_growth",
        "name": "爆款传播型",
        "description": "适合热点评论、传播和涨粉文章。",
        "role_ids": ["title_expert", "growth_operator", "target_reader"],
        "style_ids": ["sharp", "accessible"],
        "strictness": "standard",
        "focus": "提高标题点击力、开头留存、完读、点赞和转发潜力，同时避免标题党。",
    },
    "light_story": {
        "id": "light_story",
        "name": "轻松风趣型",
        "description": "适合职场、生活和轻知识内容。",
        "role_ids": ["target_reader", "chief_editor", "growth_operator"],
        "style_ids": ["humorous", "storytelling"],
        "strictness": "standard",
        "focus": "增强开头吸引力、完读和分享意愿，但不制造段子化、低俗化表达。",
    },
    "executive_brief": {
        "id": "executive_brief",
        "name": "高管阅读型",
        "description": "重点检查结论、信息密度、方法论和行动建议。",
        "role_ids": ["chief_editor", "fact_checker", "growth_operator"],
        "style_ids": ["concise", "rigorous"],
        "strictness": "strict",
        "focus": "减少长篇铺垫，提升开头留存和完读潜力，优先呈现判断、方法和行动建议。",
    },
    "brand_safe": {
        "id": "brand_safe",
        "name": "品牌安全型",
        "description": "适合企业品牌号和对外宣传稿。",
        "role_ids": ["compliance_expert", "brand_advisor", "fact_checker"],
        "style_ids": ["rigorous", "concise"],
        "strictness": "strict",
        "focus": "优先保证事实、合规和品牌安全，同时评估标题、开头、完读、点赞和转发潜力。",
    },
}


REWRITE_MODES: dict[str, dict[str, str]] = {
    "engagement_optimization": {
        "name": "按传播目标整体优化",
        "description": "围绕标题、开头、完读、点赞和转发进行必要的整体调整，不逐段润色。",
    },
    "selected_issues": {
        "name": "保持当前风格，只修复勾选问题",
        "description": "只处理人工勾选的可自动修改建议。",
    },
    "role_guided": {
        "name": "按评审角色建议修改",
        "description": "依据所选评审角色的意见修改，不额外改变文章定位。",
    },
    "target_style": {
        "name": "改成目标风格",
        "description": "在修复问题的同时，更明显地向目标风格靠拢。",
    },
    "high_priority": {
        "name": "只修改高优先级问题",
        "description": "忽略中低优先级建议，降低修改范围。",
    },
    "title_only": {
        "name": "只修改标题",
        "description": "正文保持不变，只处理标题和副标题建议。",
    },
    "selected_paragraphs": {
        "name": "只修改指定段落",
        "description": "仅允许修改指定段落，其他段落必须原样保留。",
    },
    "full_rewrite": {
        "name": "全文重新改写",
        "description": "保留事实和核心主题，按评审结果生成完整新版本。",
    },
}


DEFAULT_REVIEW_SCHEME_ID = "professional_depth"


def review_options() -> dict[str, Any]:
    return {
        "roles": [
            {"id": key, **value} for key, value in REVIEW_ROLES.items()
        ],
        "styles": [
            {"id": key, **value} for key, value in REVIEW_STYLES.items()
        ],
        "schemes": [dict(value) for value in BUILTIN_REVIEW_SCHEMES.values()],
        "strictness_levels": [
            {"id": key, **value}
            for key, value in STRICTNESS_LEVELS.items()
        ],
        "rewrite_modes": [
            {"id": key, **value} for key, value in REWRITE_MODES.items()
        ],
        "core_dimensions": [dict(item) for item in ENGAGEMENT_REVIEW_DIMENSIONS],
        "priority_order": ["事实与合规底线", "公众号品牌规则", "用户目标风格"],
    }


def normalize_review_config(
    value: dict[str, Any] | None,
    *,
    default_scheme_id: str = DEFAULT_REVIEW_SCHEME_ID,
) -> dict[str, Any]:
    source = dict(value or {})
    scheme_id = str(source.get("scheme_id") or default_scheme_id).strip()
    scheme = BUILTIN_REVIEW_SCHEMES.get(
        scheme_id,
        BUILTIN_REVIEW_SCHEMES[DEFAULT_REVIEW_SCHEME_ID],
    )
    role_ids = _known_ids(
        source.get("role_ids"),
        REVIEW_ROLES,
        list(scheme["role_ids"]),
    )
    style_ids = _known_ids(
        source.get("style_ids"),
        REVIEW_STYLES,
        list(scheme["style_ids"]),
    )
    strictness = str(source.get("strictness") or scheme["strictness"])
    if strictness not in STRICTNESS_LEVELS:
        strictness = "standard"
    requested_permissions = dict(source.get("permissions") or {})
    role_allows_title = any(
        REVIEW_ROLES[item].get("default_scope") in {"title", "article"}
        and bool(REVIEW_ROLES[item].get("may_rewrite"))
        for item in role_ids
    )
    role_allows_body = any(
        REVIEW_ROLES[item].get("default_scope") == "article"
        and bool(REVIEW_ROLES[item].get("may_rewrite"))
        for item in role_ids
    )
    hard_policy = {
        # 事实与合规是所有方案共同的发布底线；选中对应角色只会加深检查，
        # 不选也不能关闭最低限度风险扫描或把风险交给 AI 擅自改写。
        "fact_advisory_only": True,
        "compliance_advisory_only": True,
        "can_block_draft": True,
    }
    permissions = {
        "allow_rewrite": True,
        "allow_title_changes": role_allows_title,
        "allow_body_changes": role_allows_body,
        "default_scope": "article",
        **hard_policy,
    }
    # 用户配置只能收紧权限，不能关闭事实/合规底线或扩大角色改写范围。
    for key in ("allow_rewrite", "allow_title_changes", "allow_body_changes"):
        if key in requested_permissions:
            permissions[key] = bool(permissions[key]) and bool(
                requested_permissions[key]
            )
    if str(requested_permissions.get("default_scope") or "") in {
        "article",
        "title",
        "selected_paragraphs",
    }:
        permissions["default_scope"] = str(requested_permissions["default_scope"])
    return {
        "scheme_id": scheme_id if scheme_id in BUILTIN_REVIEW_SCHEMES else "custom",
        "name": _text(source.get("name") or scheme["name"], 80),
        "description": _text(
            source.get("description") or scheme.get("description") or "",
            500,
        ),
        "role_ids": role_ids,
        "style_ids": style_ids,
        "strictness": strictness,
        "focus": _text(source.get("focus") or scheme.get("focus") or "", 4000),
        "target_audience": _text(source.get("target_audience"), 1000),
        "required_checks": _lines(source.get("required_checks"), 30, 500),
        "ignored_items": _lines(source.get("ignored_items"), 30, 500),
        "banned_expressions": _lines(
            source.get("banned_expressions"), 100, 200
        ),
        "must_keep": _lines(source.get("must_keep"), 50, 500),
        "dimension_strictness": {
            str(key): str(item)
            for key, item in dict(source.get("dimension_strictness") or {}).items()
            if str(item) in STRICTNESS_LEVELS
        },
        "score_weights": {
            str(key): max(0, min(100, int(item)))
            for key, item in dict(source.get("score_weights") or {}).items()
            if str(item).strip().lstrip("-").isdigit()
        },
        "good_example": _text(source.get("good_example"), 6000),
        "bad_example": _text(source.get("bad_example"), 6000),
        "advanced_rules": _text(source.get("advanced_rules"), 8000),
        "permissions": permissions,
    }


def _known_ids(
    values: Any,
    allowed: dict[str, Any],
    fallback: list[str],
) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return fallback
    result = list(
        dict.fromkeys(
            str(item).strip()
            for item in values
            if str(item).strip() in allowed
        )
    )
    return result or fallback


def _lines(value: Any, limit: int, item_limit: int) -> list[str]:
    if isinstance(value, str):
        items = value.replace("\r\n", "\n").split("\n")
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = []
    return list(
        dict.fromkeys(
            _text(item, item_limit)
            for item in items
            if _text(item, item_limit)
        )
    )[:limit]


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]
