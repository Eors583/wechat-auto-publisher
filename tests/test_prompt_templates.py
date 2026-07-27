from __future__ import annotations

import pytest

from app.ai import RewriteResult
from app.ai.failover import FailoverRewriter
from app.accounts import (
    apply_account_selection,
    save_account,
    save_account_prompt_selection,
)
from app.ai.model_registry import OPENAI_COMPATIBLE, save_model
from app.db import Database
from app.cover.generator import build_cover_prompt
from app.inline_images import plan_inline_images
from app.layout_profiles import normalize_layout, validate_layout
from app.prompt_templates import (
    ARTICLE_PROMPT_PURPOSE,
    DEFAULT_IMAGE_PROMPT_STYLE,
    IMAGE_PROMPT_PURPOSE,
    PROMPT_MODE_DEFAULT,
    PROMPT_MODE_TEMPLATE,
    delete_prompt_template,
    public_prompt_templates,
    resolve_article_prompt_instructions,
    resolve_image_prompt_style,
    save_prompt_template,
)


def test_default_image_prompt_is_fixed_in_code(tmp_path) -> None:
    db = Database(tmp_path / "prompts.db")
    layout = normalize_layout({})
    style, mode, name = resolve_image_prompt_style(layout["inline_images"], db)

    assert style == DEFAULT_IMAGE_PROMPT_STYLE
    assert mode == PROMPT_MODE_DEFAULT
    assert name == "默认模板"


def test_custom_prompt_template_can_be_saved_and_resolved(tmp_path) -> None:
    db = Database(tmp_path / "prompts.db")
    template_id = save_prompt_template(
        db,
        name="深蓝科技纪实风",
        content="真实科技产业现场，深蓝和青绿色调，自然光线，主体明确",
    )

    templates = public_prompt_templates(db, enabled_only=True)
    assert [item["id"] for item in templates] == [template_id]
    style, mode, name = resolve_image_prompt_style(
        {
            "prompt_mode": PROMPT_MODE_TEMPLATE,
            "prompt_template_id": template_id,
        },
        db,
    )
    assert "深蓝和青绿色调" in style
    assert mode == PROMPT_MODE_TEMPLATE
    assert name == "深蓝科技纪实风"


def test_template_in_use_cannot_be_disabled_or_deleted(tmp_path) -> None:
    db = Database(tmp_path / "prompts.db")
    template_id = save_prompt_template(
        db,
        name="企业新闻风",
        content="真实企业新闻摄影，画面自然克制",
    )
    db.upsert_official_account(
        {
            "id": "account-1",
            "name": "公众号A",
            "app_id": "wx-test",
            "app_secret_encrypted": "encrypted",
            "model_id": "model-1",
            "enabled": True,
            "layout": {
                "inline_images": {
                    "prompt_mode": PROMPT_MODE_TEMPLATE,
                    "prompt_template_id": template_id,
                }
            },
        }
    )

    with pytest.raises(ValueError, match="公众号A"):
        save_prompt_template(
            db,
            template_id=template_id,
            name="企业新闻风",
            content="更新后的内容",
            enabled=False,
        )
    with pytest.raises(ValueError, match="公众号A"):
        delete_prompt_template(db, template_id)


def test_layout_requires_template_selection_in_custom_mode() -> None:
    with pytest.raises(ValueError, match="必须选择一个提示词模板"):
        validate_layout(
            {
                "inline_images": {
                    "prompt_mode": PROMPT_MODE_TEMPLATE,
                    "prompt_template_id": "",
                }
            }
        )


def test_different_accounts_can_select_different_prompt_templates(tmp_path) -> None:
    db = Database(tmp_path / "prompts.db")
    template_a = save_prompt_template(
        db, name="科技纪实", content="真实科技研发现场，冷色调"
    )
    template_b = save_prompt_template(
        db, name="家族办公室", content="克制稳健的财富管理场景，暖色调"
    )
    for account_id, name in (("account-a", "公众号A"), ("account-b", "公众号B")):
        db.upsert_official_account(
            {
                "id": account_id,
                "name": name,
                "app_id": f"wx-{account_id}",
                "app_secret_encrypted": "encrypted",
                "model_id": "model-1",
                "enabled": True,
                "layout": normalize_layout({}),
            }
        )

    assert save_account_prompt_selection(db, "account-a", template_a) == "科技纪实"
    assert save_account_prompt_selection(db, "account-b", template_b) == "家族办公室"

    account_a = db.get_official_account("account-a")
    account_b = db.get_official_account("account-b")
    assert template_a in str(account_a["layout_json"])
    assert template_b not in str(account_a["layout_json"])
    assert template_b in str(account_b["layout_json"])

    assert save_account_prompt_selection(db, "account-a", None) == "默认模板"
    assert '"prompt_mode": "default"' in str(
        db.get_official_account("account-a")["layout_json"]
    )


def test_article_and_image_prompt_catalogs_and_account_bindings_are_isolated(
    tmp_path,
) -> None:
    db = Database(tmp_path / "prompts.db")
    model_id = save_model(
        db,
        name="模板测试模型",
        provider_type=OPENAI_COMPATIBLE,
        api_base="https://llm.example.test/v1",
        model="prompt-model",
        api_key="secret",
    )
    account_id = save_account(
        db,
        name="双模板公众号",
        app_id="wx-prompt-account",
        app_secret="wechat-secret",
        model_id=model_id,
    )
    article_template = save_prompt_template(
        db,
        name="经营者深度评论",
        content="ARTICLE_MARKER：面向企业经营者，观点务实并提供经营案例。",
        purpose=ARTICLE_PROMPT_PURPOSE,
    )
    image_template = save_prompt_template(
        db,
        name="深蓝纪实配图",
        content="IMAGE_MARKER：深蓝色调的真实商业新闻摄影。",
        purpose=IMAGE_PROMPT_PURPOSE,
    )

    assert [item["id"] for item in public_prompt_templates(
        db, purpose=ARTICLE_PROMPT_PURPOSE
    )] == [article_template]
    assert [item["id"] for item in public_prompt_templates(
        db, purpose=IMAGE_PROMPT_PURPOSE
    )] == [image_template]

    save_account_prompt_selection(
        db,
        account_id,
        article_template,
        purpose=ARTICLE_PROMPT_PURPOSE,
    )
    save_account_prompt_selection(
        db,
        account_id,
        image_template,
        purpose=IMAGE_PROMPT_PURPOSE,
    )
    with pytest.raises(ValueError, match="图片提示词模板"):
        save_account_prompt_selection(
            db,
            account_id,
            article_template,
            purpose=IMAGE_PROMPT_PURPOSE,
        )
    with pytest.raises(ValueError, match="文章提示词模板"):
        save_account_prompt_selection(
            db,
            account_id,
            image_template,
            purpose=ARTICLE_PROMPT_PURPOSE,
        )
    with pytest.raises(ValueError, match="双模板公众号"):
        save_prompt_template(
            db,
            template_id=article_template,
            name="经营者深度评论",
            content="更新后的文章要求",
            enabled=False,
            purpose=ARTICLE_PROMPT_PURPOSE,
        )

    effective, _ = apply_account_selection(
        {
            "ai": {
                "rewrite_prompt": "BASE_REWRITE_PROTOCOL",
                "title_prompt": "BASE_TITLE_PROTOCOL",
            }
        },
        db,
        account_id,
    )
    assert "BASE_REWRITE_PROTOCOL" in effective["ai"]["rewrite_prompt"]
    assert "ARTICLE_MARKER" in effective["ai"]["rewrite_prompt"]
    assert "BASE_TITLE_PROTOCOL" in effective["ai"]["title_prompt"]
    assert "ARTICLE_MARKER" in effective["ai"]["title_prompt"]
    assert "IMAGE_MARKER" not in effective["ai"]["rewrite_prompt"]
    assert "IMAGE_MARKER" in effective["inline_images"]["prompt_style"]
    assert "ARTICLE_MARKER" not in effective["inline_images"]["prompt_style"]

    plans = plan_inline_images(
        "## 经营系统必须形成闭环\n\n经营目标、组织动作和复盘机制需要彼此衔接。" * 20,
        prompt_style=effective["inline_images"]["prompt_style"],
    )
    assert plans and "IMAGE_MARKER" in plans[0].prompt
    cover_prompt = build_cover_prompt(
        title="经营系统如何形成闭环",
        body="## 经营系统必须形成闭环\n\n经营目标与组织动作需要衔接。",
        prompt_style=effective["inline_images"]["prompt_style"],
    )
    assert "深蓝色调的真实商业新闻摄影" in cover_prompt

    captured: dict[str, str] = {}

    class FakeLongformClient:
        def rewrite_longform(
            self,
            _topic: str,
            _raw_content: str,
            *,
            instruction: str,
            title_instruction: str,
            **_kwargs,
        ) -> RewriteResult:
            captured["instruction"] = instruction
            captured["title_instruction"] = title_instruction
            return RewriteResult(
                body="全新经营观点。" * 400,
                titles=[
                    f"全新经营标题{index}" for index in range(1, 11)
                ],
                subtitles=[
                    f"全新经营副标题{index}" for index in range(1, 11)
                ],
            )

    rewriter = FailoverRewriter(effective)
    rewriter._clients[rewriter.primary] = FakeLongformClient()
    rewriter.rewrite("经营系统", "很短的参考资料")
    assert "ARTICLE_MARKER" in captured["instruction"]
    assert "ARTICLE_MARKER" in captured["title_instruction"]

    save_account_prompt_selection(
        db,
        account_id,
        None,
        purpose=ARTICLE_PROMPT_PURPOSE,
    )
    reset_effective, _ = apply_account_selection(
        {"ai": {"rewrite_prompt": "BASE", "title_prompt": "TITLE"}},
        db,
        account_id,
    )
    assert "ARTICLE_MARKER" not in reset_effective["ai"]["rewrite_prompt"]
    assert "IMAGE_MARKER" in reset_effective["inline_images"]["prompt_style"]


def test_article_prompt_default_and_custom_resolution_preserve_system_protocol(
    tmp_path,
) -> None:
    db = Database(tmp_path / "prompts.db")
    default = resolve_article_prompt_instructions(
        {"prompt_mode": PROMPT_MODE_DEFAULT},
        db,
        rewrite_instruction="SYSTEM_REWRITE",
        title_instruction="SYSTEM_TITLE",
    )
    assert default == ("SYSTEM_REWRITE", "SYSTEM_TITLE", "default", "默认模板")

    template_id = save_prompt_template(
        db,
        name="文章调性",
        content="CUSTOM_ARTICLE_TONE",
        purpose=ARTICLE_PROMPT_PURPOSE,
    )
    rewrite, title, mode, name = resolve_article_prompt_instructions(
        {
            "prompt_mode": PROMPT_MODE_TEMPLATE,
            "prompt_template_id": template_id,
        },
        db,
        rewrite_instruction="SYSTEM_REWRITE",
        title_instruction="SYSTEM_TITLE",
    )
    assert "SYSTEM_REWRITE" in rewrite and "CUSTOM_ARTICLE_TONE" in rewrite
    assert "SYSTEM_TITLE" in title and "CUSTOM_ARTICLE_TONE" in title
    assert mode == PROMPT_MODE_TEMPLATE
    assert name == "文章调性"
