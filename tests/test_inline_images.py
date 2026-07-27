from __future__ import annotations

from app.inline_images import (
    ImagePlan,
    build_inline_image_revision_prompt,
    create_argument_card,
    invalidate_inline_image_meta,
    insert_inline_images,
    is_useful_source_image_url,
    plan_inline_images,
    remove_inline_image,
)


def _body() -> str:
    sections = []
    for index in range(1, 13):
        if index in {3, 7, 10}:
            lead = f"案例{index}的数据表明，企业利润增长了{index * 8}%，这是一个关键行动建议。"
        else:
            lead = f"第{index}部分讨论企业经营过程中的组织协同与长期能力建设。"
        sections.append(lead + "经营者需要结合具体场景判断。" * 12)
    return "\n\n".join(sections)


def test_plan_inline_images_respects_count_and_spacing() -> None:
    plans = plan_inline_images(
        _body(), min_count=2, max_count=4, min_spacing=500, max_spacing=850
    )
    assert 2 <= len(plans) <= 4
    assert all(b.offset - a.offset >= 500 for a, b in zip(plans, plans[1:]))
    assert all(plan.keywords for plan in plans)
    assert all("新闻纪实摄影" in plan.prompt for plan in plans)


def test_insert_and_remove_inline_image() -> None:
    html = "<section><p>这是案例数据与关键建议的完整内容。</p><p>后续正文。</p></section>"
    asset = {
        "index": 1,
        "anchor": "这是案例数据与关键建议的完整内容。",
        "url": "https://mmbiz.qpic.cn/example.jpg",
        "caption": "案例数据与关键建议",
    }
    inserted = insert_inline_images(html, [asset])
    assert 'data-inline-image-id="1"' in inserted
    assert "https://mmbiz.qpic.cn/example.jpg" in inserted
    assert "案例数据与关键建议" in inserted
    removed = remove_inline_image(inserted, 1)
    assert "data-inline-image-id" not in removed
    assert "example.jpg" not in removed
    assert "后续正文" in removed


def test_single_image_revision_prompt_combines_feedback_and_hard_safeguards() -> None:
    prompt = build_inline_image_revision_prompt(
        {
            "index": 2,
            "caption": "供应链库存周转",
            "anchor": "关键结论是库存积压正在侵蚀现金流。",
            "keywords": ["库存", "现金流"],
            "prompt": "现代供应链业务现场，写实新闻摄影。",
        },
        "不要会议室，改成仓库盘点现场",
        article_title="库存管理为什么决定现金流",
    )

    assert "库存管理为什么决定现金流" in prompt
    assert "库存积压正在侵蚀现金流" in prompt
    assert "不要会议室" in prompt
    assert "不得出现任何可读文字" in prompt
    assert "边框、留白" in prompt


def test_plan_prefers_end_of_argument_sections() -> None:
    body = """
## 论点一：组织能力

这是论点一的开场说明，企业需要先理解组织能力的来源和限制。经营实践需要持续观察。

这是论点一的收束段落，经过前面的分析，最终行动建议是先明确责任边界再调整流程。这里结束第一个完整论点。

## 论点二：数据验证

这是论点二的展开部分，案例数据显示利润增长了32%，但管理者仍需判断增长质量。

这是论点二的收束段落，关键结论是数据必须与业务场景结合，不能单独追求指标。这里结束第二个完整论点。

## 结语

全文总结段落不应优先在文章末尾追加图片，因为它不是新的论点展开位置。
"""
    plans = plan_inline_images(
        body, min_count=2, max_count=2, min_spacing=50, max_spacing=500
    )
    anchors = " ".join(plan.anchor for plan in plans)
    assert "结束第一个完整论点" in anchors
    assert "结束第二个完整论点" in anchors
    assert "全文总结段落" not in anchors


def test_plan_places_one_image_at_every_argument_end() -> None:
    body = """
## 论点一：组织能力

第一部分展开组织协同的背景和限制，管理者需要明确责任边界。

第一部分最终结论是先校准组织目标，再逐步调整协作流程。

## 论点二：经营数据

第二部分分析收入、利润与现金流之间的关系，关键数据需要交叉验证。

第二部分最终建议是建立指标看板，并持续检查数据质量。

## 论点三：行动机制

第三部分讨论执行节奏，团队需要把战略转化为可追踪的具体动作。

第三部分最终行动是明确负责人、完成时间和复盘方式。

## 结语

全文总结不属于新的论点，不应在结语后继续添加图片。
"""
    plans = plan_inline_images(
        body,
        min_count=1,
        max_count=6,
        min_spacing=2000,
        prompt_style="深蓝色科技商业摄影风格",
    )
    assert len(plans) == 3
    assert "第一部分最终结论" in plans[0].anchor
    assert "第二部分最终建议" in plans[1].anchor
    assert "第三部分最终行动" in plans[2].anchor
    assert all("深蓝色科技商业摄影风格" in plan.prompt for plan in plans)
    assert all("全文总结" not in plan.anchor for plan in plans)


def test_argument_count_is_not_capped_by_fallback_max_count() -> None:
    body = "\n\n".join(
        f"## 论点{index}\n\n这是第{index}个论点的分析。\n\n这是第{index}个论点的最终结论和行动建议。"
        for index in range(1, 6)
    )
    plans = plan_inline_images(
        body,
        min_count=1,
        max_count=2,
        min_spacing=2000,
        placement_mode="argument_end",
    )
    assert len(plans) == 5
    assert all(f"第{index}个论点的最终结论" in plans[index - 1].anchor for index in range(1, 6))


def test_nested_subheadings_do_not_create_extra_argument_images() -> None:
    body = """
## 主论点一

主论点一的背景分析、业务场景、关键数据与最终行动结论都在这里完整说明。

### 子论点一

主论点一下面的补充说明，用来解释具体业务背景和执行条件。

### 子论点二

主论点一下面的第二项补充说明，用来解释相关数据和影响因素。

## 主论点二

主论点二的背景分析、关键影响因素与最终行动建议都在这里完整说明。

### 子论点三

主论点二下面的补充说明，用来解释具体策略和实施条件。
"""
    plans = plan_inline_images(body, min_count=1, max_count=8, min_spacing=50)
    assert len(plans) == 2
    assert plans[0].caption.startswith("主论点一")
    assert plans[1].caption.startswith("主论点二")
    assert "第二项补充说明" in plans[0].anchor
    assert "具体策略和实施条件" in plans[1].anchor
    assert "主论点一下面的第二项补充说明" not in plans[0].prompt
    assert "画面从边缘到边缘铺满" in plans[0].prompt
    assert "一个连续场景" in plans[0].prompt
    assert "【视觉摘要】" not in plans[0].prompt
    assert "【论点标题】" not in plans[0].prompt
    assert len(plans[0].prompt) < 500


def test_film_argument_gets_concrete_visual_scene_without_text_layout() -> None:
    plans = plan_inline_images(
        """
## 影视公司的创新发展

一家互联网视频公司在成都成立影视文化公司，业务包括电影摄制、文艺创作和电视剧制作。

### 内容生产升级

新的制作团队通过专业摄影设备和数字化流程完成影视内容生产。
"""
    )
    assert len(plans) == 1
    prompt = plans[0].prompt
    assert "专业影视拍摄现场" in prompt
    assert "摄影机、灯光、布景" in prompt
    assert "一个连续场景" in prompt
    assert "不可辨认的虚化细节" in prompt


def test_film_production_line_metaphor_stays_in_one_film_scene() -> None:
    plans = plan_inline_images(
        """
## 影视公司背后的战略逻辑

一家互联网视频公司在成都建立影视内容制作团队。

### 精益化生产

结论：这是一次把野心从宏大生态转向高效产能的重启，精益生产线只是管理比喻。
"""
    )
    prompt = plans[0].prompt
    assert "专业影视拍摄现场" in prompt
    assert "现代制造或供应链现场" not in prompt
    assert "结论：" not in prompt
    assert "宏大生态转向高效产能" in prompt


def test_filters_known_36kr_brand_placeholder() -> None:
    assert not is_useful_source_image_url(
        "https://img.36krcdn.com/20191024/v2_1571894049839_img_jpg"
    )
    assert not is_useful_source_image_url("https://example.com/assets/site-logo.png")
    assert is_useful_source_image_url(
        "https://img.36krcdn.com/hsossms/20260720/article_scene_img_png"
    )


def test_invalidate_inline_images_after_body_edit() -> None:
    result = invalidate_inline_image_meta(
        {
            "inline_images_resolved": True,
            "inline_images": [{"index": 1, "url": "https://example.com/old.jpg"}],
            "inline_image_warnings": ["旧提示"],
            "unrelated": "保留",
        }
    )
    assert result["inline_images_resolved"] is False
    assert result["inline_images"] == []
    assert result["inline_image_warnings"] == []
    assert result["unrelated"] == "保留"


def test_create_argument_card(tmp_path) -> None:
    target = tmp_path / "card.png"
    create_argument_card(
        ImagePlan(
            index=1,
            anchor="论点收束段落",
            offset=600,
            keywords=["组织能力", "关键数据", "行动建议"],
            caption="组织能力决定企业长期增长质量",
            prompt="",
        ),
        target,
    )
    assert target.exists()
    assert target.stat().st_size > 10_000
