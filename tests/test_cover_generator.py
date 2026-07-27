from __future__ import annotations

from PIL import Image

from app.ai.model_registry import OPENAI_IMAGE, encrypt_api_key
from app.cover import generator
from app.db import Database


def test_cover_prompt_uses_title_body_and_arguments_without_poster_layout() -> None:
    prompt = generator.build_cover_prompt(
        title="AI 经营中心正在重塑企业决策",
        body="""
## 从经验判断走向实时决策

企业把经营数据接入统一平台，因此管理者能够更快识别风险并调整资源。

## 组织协同方式发生变化

跨部门团队围绕同一目标协作，关键是缩短信息到行动的距离。

## 结语

这是一场长期转型。
""",
        prompt_style="写实商业杂志风格",
    )

    assert "AI 经营中心正在重塑企业决策" in prompt
    assert "从经验判断走向实时决策" in prompt
    assert "组织协同方式发生变化" in prompt
    assert "结语" not in prompt
    assert "新闻纪实摄影风格" in prompt
    assert "不得出现任何可读文字" in prompt
    assert "2.35:1" in prompt
    assert len(prompt) < 1000


def test_cover_prompt_accepts_operator_revision_without_relaxing_no_text_rule() -> None:
    prompt = generator.build_cover_prompt(
        title="供应链效率决定现金流",
        body="## 库存周转\n\n企业需要在真实仓储流程中降低积压。",
        instruction="不要会议室，改成现代仓库盘点现场，人物位于画面中间",
    )

    assert "现代仓库盘点现场" in prompt
    assert "人物位于画面中间" in prompt
    assert "不得出现任何可读文字" in prompt


def test_generate_cover_uploads_permanent_material_and_crops_image(
    tmp_path, monkeypatch
) -> None:
    db = Database(tmp_path / "cover.db")
    db.upsert_ai_model(
        {
            "id": "image-1",
            "name": "测试生图智能体",
            "provider_type": OPENAI_IMAGE,
            "api_base": "https://images.example.test/v1",
            "model": "image-model",
            "api_key_encrypted": encrypt_api_key("secret"),
            "enabled": True,
        }
    )
    captured: dict[str, object] = {}

    def fake_generate_image(**kwargs):
        captured.update(kwargs)
        target = kwargs["output_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1920, 1080), (12, 90, 130)).save(target, "JPEG")
        return target

    def fake_upload(_client, path):
        with Image.open(path) as image:
            captured["uploaded_size"] = image.size
        return {"media_id": "cover-media-1", "url": "https://example.test/cover.jpg"}

    monkeypatch.setattr(generator, "generate_image", fake_generate_image)
    monkeypatch.setattr(generator, "upload_permanent_image", fake_upload)

    result = generator.generate_article_cover(
        title="企业增长的新逻辑",
        body="## 核心变化\n\n数据驱动团队把洞察更快转化为行动。",
        settings={
            "image_model_id": "image-1",
            "prompt_style": "新闻纪实摄影",
        },
        db=db,
        client=object(),
        root=tmp_path,
        job_id=12,
    )

    assert result["media_id"] == "cover-media-1"
    assert result["url"] == "https://example.test/cover.jpg"
    assert result["model_name"] == "测试生图智能体"
    assert captured["uploaded_size"] == (1410, 600)
    assert "企业增长的新逻辑" in str(captured["prompt"])


def test_invalidate_only_active_generated_cover() -> None:
    meta, cleared = generator.invalidate_generated_cover(
        {
            "generated_cover_active": True,
            "generated_cover": {"media_id": "generated"},
            "other": "keep",
        }
    )
    assert cleared is True
    assert "generated_cover" not in meta
    assert meta["other"] == "keep"

    manual_meta, manual_cleared = generator.invalidate_generated_cover(
        {"generated_cover_active": False, "generated_cover": {"media_id": "history"}}
    )
    assert manual_cleared is False
    assert manual_meta["generated_cover"]["media_id"] == "history"
