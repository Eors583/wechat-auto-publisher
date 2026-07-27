from __future__ import annotations

import json
import sqlite3

import pytest

from app.accounts import save_account_prompt_selection
from app.ai.image_providers import IMAGE_ALIBABA
from app.db import Database
from app.editorial_review import DEFAULT_REVIEW_SCHEME_ID
from app.prompt_templates import (
    ARTICLE_PROMPT_PURPOSE,
    IMAGE_PROMPT_PURPOSE,
    save_prompt_template,
)
from app.services.creation_plans import (
    BUILTIN_DEFAULT_CREATION_PLAN_ID,
    CreationPlanService,
)


def _db(tmp_path) -> Database:
    db = Database(tmp_path / "creation-plans.db")
    db.upsert_official_account(
        {
            "id": "account-1",
            "name": "蓝血研究",
            "app_id": "wx-app",
            "app_secret_encrypted": "encrypted-secret",
            "model_id": "model-1",
            "enabled": True,
        }
    )
    return db


def _prompt(
    db: Database,
    *,
    name: str,
    purpose: str,
) -> str:
    return save_prompt_template(
        db,
        name=name,
        content=f"{name}的业务规则",
        purpose=purpose,
    )


def _add_image_model(db: Database, model_id: str = "image-1") -> str:
    db.upsert_ai_model(
        {
            "id": model_id,
            "name": "通义万相",
            "provider_type": IMAGE_ALIBABA,
            "api_base": "",
            "model": "wan2.6-t2i",
            "api_key_encrypted": "encrypted-key",
            "enabled": True,
        }
    )
    return model_id


def test_builtin_plan_and_unbound_legacy_account_are_compatible(tmp_path) -> None:
    db = _db(tmp_path)
    service = CreationPlanService(db)

    plans = service.list()
    default = plans[0]
    account_default = service.get_account_default("account-1")

    assert default["id"] == BUILTIN_DEFAULT_CREATION_PLAN_ID
    assert default["builtin"] is True
    assert default["available"] is True
    assert account_default["bound"] is False
    assert account_default["compatibility_mode"] is True
    assert account_default["plan"] is None
    assert account_default["effective_configuration"] == {
        "article_prompt_template_id": "",
        "image_prompt_template_id": "",
        "editorial_review_profile_id": DEFAULT_REVIEW_SCHEME_ID,
    }


def test_save_and_list_creation_plan_with_all_references(tmp_path) -> None:
    db = _db(tmp_path)
    article_id = _prompt(
        db,
        name="深度文章",
        purpose=ARTICLE_PROMPT_PURPOSE,
    )
    image_id = _prompt(
        db,
        name="商务配图",
        purpose=IMAGE_PROMPT_PURPOSE,
    )
    service = CreationPlanService(db)

    plan = service.save(
        name="蓝血研究深度方案",
        description="深度文章和高管阅读评审",
        article_prompt_template_id=article_id,
        image_prompt_template_id=image_id,
        editorial_review_profile_id="executive_brief",
    )

    assert plan["article_prompt_template_id"] == article_id
    assert plan["article_prompt_template_name"] == "深度文章"
    assert plan["image_prompt_template_id"] == image_id
    assert plan["image_prompt_template_name"] == "商务配图"
    assert plan["editorial_review_profile_id"] == "executive_brief"
    assert plan["editorial_review_profile_name"] == "高管阅读型"
    assert plan["available"] is True
    assert service.get(plan["id"]) == plan
    assert [item["id"] for item in service.list(include_builtin=False)] == [
        plan["id"]
    ]


def test_save_rejects_prompt_template_of_wrong_purpose(tmp_path) -> None:
    db = _db(tmp_path)
    image_id = _prompt(
        db,
        name="图片规则",
        purpose=IMAGE_PROMPT_PURPOSE,
    )
    service = CreationPlanService(db)

    with pytest.raises(ValueError, match="文章提示词模板"):
        service.save(
            name="错误方案",
            article_prompt_template_id=image_id,
        )

    assert service.list(include_builtin=False) == []


def test_apply_updates_existing_account_settings_and_tracks_sync(tmp_path) -> None:
    db = _db(tmp_path)
    article_id = _prompt(
        db,
        name="文章模板",
        purpose=ARTICLE_PROMPT_PURPOSE,
    )
    image_id = _prompt(
        db,
        name="图片模板",
        purpose=IMAGE_PROMPT_PURPOSE,
    )
    service = CreationPlanService(db)
    plan = service.save(
        name="公众号完整方案",
        article_prompt_template_id=article_id,
        image_prompt_template_id=image_id,
        editorial_review_profile_id="brand_safe",
    )

    result = service.apply_to_account("account-1", plan["id"])

    account = db.get_official_account("account-1")
    layout = json.loads(str(account["layout_json"]))
    review = service.reviews.get_account_default("account-1")
    assert layout["article_prompt"]["prompt_template_id"] == article_id
    assert layout["inline_images"]["prompt_template_id"] == image_id
    assert review["profile_id"] == "brand_safe"
    assert result["applied"] is True
    assert result["bound"] is True
    assert result["in_sync"] is True
    assert result["plan_id"] == plan["id"]

    # A later manual change remains supported and is surfaced as plan drift.
    save_account_prompt_selection(
        db,
        "account-1",
        None,
        purpose=ARTICLE_PROMPT_PURPOSE,
    )
    drifted = service.get_account_default("account-1")
    assert drifted["bound"] is True
    assert drifted["in_sync"] is False
    assert (
        drifted["effective_configuration"]["article_prompt_template_id"] == ""
    )


def test_builtin_plan_resets_account_and_releases_custom_plan_for_delete(
    tmp_path,
) -> None:
    db = _db(tmp_path)
    article_id = _prompt(
        db,
        name="临时文章模板",
        purpose=ARTICLE_PROMPT_PURPOSE,
    )
    service = CreationPlanService(db)
    plan = service.save(
        name="临时方案",
        article_prompt_template_id=article_id,
        editorial_review_profile_id="viral_growth",
    )
    service.apply_to_account("account-1", plan["id"])

    with pytest.raises(ValueError, match="正被公众号使用"):
        service.delete(plan["id"])

    reset = service.apply_to_account(
        "account-1",
        BUILTIN_DEFAULT_CREATION_PLAN_ID,
    )
    deleted = service.delete(plan["id"])

    assert reset["plan_id"] == BUILTIN_DEFAULT_CREATION_PLAN_ID
    assert reset["in_sync"] is True
    assert reset["effective_configuration"] == {
        "article_prompt_template_id": "",
        "image_prompt_template_id": "",
        "editorial_review_profile_id": DEFAULT_REVIEW_SCHEME_ID,
    }
    assert deleted == {"id": plan["id"], "deleted": True}


def test_disabled_plan_cannot_be_applied(tmp_path) -> None:
    db = _db(tmp_path)
    service = CreationPlanService(db)
    plan = service.save(name="暂停方案", enabled=False)

    with pytest.raises(ValueError, match="已停用"):
        service.apply_to_account("account-1", plan["id"])

    assert service.get_account_default("account-1")["bound"] is False
    assert service.list(enabled_only=True) == [service.get(
        BUILTIN_DEFAULT_CREATION_PLAN_ID
    )]


def test_plan_applies_layout_and_image_cover_rules_without_touching_template(
    tmp_path,
) -> None:
    db = _db(tmp_path)
    image_model_id = _add_image_model(db)
    account = db.get_official_account("account-1")
    account["layout"] = {
        "editor_template": {
            "enabled": False,
            "selected_title": "账号原模板",
            "selected_media_id": "account-1-media",
            "selected_article_index": 1,
            "placeholder": "公众号正文",
        },
        "body": {"font_size": "15px", "color": "#333333"},
    }
    db.upsert_official_account(account)
    service = CreationPlanService(db, config={"_root": str(tmp_path)})
    plan = service.save(
        name="完整视觉方案",
        layout={
            "body": {
                "font_size": "18px",
                "color": "#224466",
                "line_height": "2",
                "spacing_after": "20px",
                "first_line_indent": "2em",
                "alignment": "justify",
                "horizontal_padding": "12px",
            },
            "argument": {
                "color": "#008577",
                "background": "#f2fffb",
            },
        },
        image_settings={
            "enabled": True,
            "generate_cover": True,
            "min_count": 3,
            "max_count": 5,
            "min_spacing": 700,
            "max_spacing": 1000,
            "source_mode": "generate",
            "placement_mode": "argument_end",
            "image_model_id": image_model_id,
            "generation_concurrency": 2,
        },
    )

    result = service.apply_to_account("account-1", plan["id"])

    updated = db.get_official_account("account-1")
    layout = json.loads(str(updated["layout_json"]))
    assert layout["body"]["font_size"] == "18px"
    assert layout["body"]["first_line_indent"] == "2em"
    assert layout["argument"]["color"] == "#008577"
    assert layout["inline_images"]["enabled"] is True
    assert layout["inline_images"]["generate_cover"] is True
    assert layout["inline_images"]["min_count"] == 3
    assert layout["inline_images"]["image_model_id"] == image_model_id
    assert layout["editor_template"]["selected_title"] == "账号原模板"
    assert layout["editor_template"]["selected_media_id"] == "account-1-media"
    assert result["draft_template_application"]["status"] == (
        "preserved_account_binding"
    )
    assert result["in_sync"] is True
    assert plan["has_layout"] is True
    assert plan["has_image_settings"] is True


def test_image_cover_rules_reject_missing_or_non_image_model(tmp_path) -> None:
    db = _db(tmp_path)
    service = CreationPlanService(db)

    with pytest.raises(ValueError, match="生图智能体"):
        service.save(
            name="缺少生图模型",
            image_settings={
                "enabled": False,
                "generate_cover": True,
                "image_model_id": "missing",
            },
        )


def test_draft_template_binding_is_restored_only_to_its_own_account(
    tmp_path,
) -> None:
    db = _db(tmp_path)
    db.upsert_official_account(
        {
            "id": "account-2",
            "name": "蓝血经营管理系统",
            "app_id": "wx-app-2",
            "app_secret_encrypted": "encrypted-secret-2",
            "model_id": "model-1",
            "enabled": True,
            "layout": {
                "editor_template": {
                    "enabled": True,
                    "capture_title": "公众号排版模板",
                    "placeholder": "公众号正文",
                    "selected_media_id": "account-2-media",
                    "selected_article_index": 0,
                    "selected_title": "账号二模板",
                }
            },
        }
    )
    first = db.get_official_account("account-1")
    first["layout"] = {
        "editor_template": {
            "enabled": True,
            "capture_title": "公众号排版模板",
            "placeholder": "公众号正文",
            "selected_media_id": "account-1-old-media",
            "selected_article_index": 2,
            "selected_title": "账号一模板",
        }
    }
    db.upsert_official_account(first)
    template_dir = tmp_path / "data" / "templates"
    template_dir.mkdir(parents=True)
    first_snapshot = (
        "<section><header>账号一页眉</header><p>公众号正文</p>"
        "<footer>账号一页尾</footer></section>"
    )
    second_snapshot = (
        "<section><header>账号二页眉</header><p>公众号正文</p></section>"
    )
    (template_dir / "account-1.html").write_text(
        first_snapshot,
        encoding="utf-8",
    )
    (template_dir / "account-2.html").write_text(
        second_snapshot,
        encoding="utf-8",
    )
    service = CreationPlanService(db, config={"_root": str(tmp_path)})
    plan = service.save(
        name="带账号一草稿模板",
        draft_template_account_id="account-1",
    )

    changed = db.get_official_account("account-1")
    changed_layout = json.loads(str(changed["layout_json"]))
    changed_layout["editor_template"].update(
        selected_media_id="account-1-new-media",
        selected_article_index=0,
        selected_title="账号一新模板",
    )
    changed["layout"] = changed_layout
    db.upsert_official_account(changed)
    (template_dir / "account-1.html").write_text(
        "<section><p>公众号正文</p><footer>新模板</footer></section>",
        encoding="utf-8",
    )

    restored = service.apply_to_account("account-1", plan["id"])
    cross_account = service.apply_to_account("account-2", plan["id"])

    restored_layout = json.loads(
        str(db.get_official_account("account-1")["layout_json"])
    )
    untouched_layout = json.loads(
        str(db.get_official_account("account-2")["layout_json"])
    )
    assert restored_layout["editor_template"]["selected_title"] == "账号一模板"
    assert restored_layout["editor_template"]["selected_media_id"] == (
        "account-1-old-media"
    )
    assert (template_dir / "account-1.html").read_text(
        encoding="utf-8"
    ) == first_snapshot
    assert restored["draft_template_application"]["status"] == (
        "restored_scoped_binding"
    )
    assert untouched_layout["editor_template"]["selected_title"] == "账号二模板"
    assert untouched_layout["editor_template"]["selected_media_id"] == (
        "account-2-media"
    )
    assert (template_dir / "account-2.html").read_text(
        encoding="utf-8"
    ) == second_snapshot
    assert cross_account["draft_template_application"]["status"] == (
        "preserved_account_binding"
    )
    binding = service.get(plan["id"])["draft_template_bindings"][0]
    assert binding["account_id"] == "account-1"
    assert binding["scope"] == "same_official_account_only"
    assert binding["source_app_id_verified"] is True
    assert binding["snapshot_verified"] is True
    assert "selected_media_id" not in binding
    assert "snapshot_html" not in binding

    # Reusing the same internal account ID with another real WeChat AppID must
    # not revive the old account's media ID or template snapshot.
    switched = db.get_official_account("account-1")
    switched_layout = json.loads(str(switched["layout_json"]))
    switched_layout["editor_template"].update(
        selected_media_id="new-real-account-media",
        selected_title="新真实公众号模板",
    )
    switched["app_id"] = "wx-completely-different"
    switched["layout"] = switched_layout
    db.upsert_official_account(switched)
    mismatch = service.apply_to_account("account-1", plan["id"])
    after_switch = json.loads(
        str(db.get_official_account("account-1")["layout_json"])
    )
    assert mismatch["draft_template_application"]["status"] == (
        "preserved_app_id_mismatch"
    )
    assert after_switch["editor_template"]["selected_media_id"] == (
        "new-real-account-media"
    )


def test_existing_creation_plan_table_is_migrated_without_data_loss(
    tmp_path,
) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE creation_plans (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                article_prompt_template_id TEXT,
                image_prompt_template_id TEXT,
                editorial_review_profile_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO creation_plans (
                id, name, enabled, created_at, updated_at
            ) VALUES ('legacy-plan', '旧方案', 1, 'before', 'before')
            """
        )

    db = Database(path)
    row = db.get_creation_plan("legacy-plan")

    assert row is not None
    assert row["name"] == "旧方案"
    assert json.loads(row["layout_json"]) == {}
    assert json.loads(row["image_settings_json"]) == {}
