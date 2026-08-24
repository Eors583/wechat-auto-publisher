from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ads.scheduler import render_ad_html, select_ad
from app.ai import (
    ARTICLE_DIGEST_PROMPT,
    build_rewrite_user_prompt,
    enforce_emphasis_rules,
    normalize_model_body,
    parse_rewrite_output,
    parse_title_output,
    quality_check,
)
from app.ai.failover import FailoverRewriter
from app.config import load_config
from app.cover import resolve_cover
from app.db import Database
from app.layout_profiles import normalize_layout, validate_layout
from app.render import TemplateRenderer, make_digest


class ParseTests(unittest.TestCase):
    def test_rewrite_prompt_requests_restrained_key_emphasis(self) -> None:
        prompt = build_rewrite_user_prompt("话题", "原文", "改写")
        self.assertIn("只加粗核心观点、关键数据和行动建议", prompt)
        self.assertIn("禁止整段加粗", prompt)
        self.assertIn("禁止连续多个段落", prompt)
        self.assertIn("digest", prompt)
        self.assertIn(ARTICLE_DIGEST_PROMPT, prompt)

    def test_emphasis_guard_removes_whole_paragraph_and_consecutive_bold(self) -> None:
        body = (
            "这段说明一个重要结论：**组织能力决定执行上限**，后面继续解释。\n\n"
            "**这一整段都不应该保持加粗**\n\n"
            "普通过渡段落。\n\n"
            "今年收入达到 **增长37.5%**，数据非常关键。\n\n"
            "下一段给出 **今天先完成三件事**，但不能连续加粗。"
        )
        cleaned = enforce_emphasis_rules(body)
        self.assertIn("**组织能力决定执行上限**", cleaned)
        self.assertIn("这一整段都不应该保持加粗", cleaned)
        self.assertNotIn("**这一整段都不应该保持加粗**", cleaned)
        self.assertIn("**增长37.5%**", cleaned)
        self.assertNotIn("**今天先完成三件事**", cleaned)
    def test_rewriter_enforces_2000_char_hard_floor(self) -> None:
        cfg = load_config()
        cfg["ai"] = dict(cfg.get("ai") or {})
        cfg["ai"]["min_body_chars"] = 10
        self.assertEqual(FailoverRewriter(cfg).min_body_chars, 2000)

    def test_double_escaped_model_body_restores_paragraphs(self) -> None:
        body = r"第一段\n\n## 组织能力决定执行上限\n\n第二段说明"
        restored = normalize_model_body(body)
        self.assertIn("\n\n## 组织能力决定执行上限\n\n", restored)
        self.assertNotIn(r"\n", restored)

    def test_single_escaped_model_line_break_is_also_restored(self) -> None:
        restored = normalize_model_body(r"第一段\n第二段")
        self.assertEqual(restored, "第一段\n第二段")

    def test_parse_rewrite_json(self) -> None:
        body = "这是一篇足够长的正文内容，用于测试解析与质检逻辑是否正常工作。" * 5
        payload = {
            "body": body,
            "titles": ["标题一", "标题二", "标题三", "标题四", "标题五"],
            "subtitles": ["副1", "副2", "副3", "副4", "副5"],
            "digest": "摘要：这是阅读全文后形成的核心概括。",
        }
        import json

        text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        result = parse_rewrite_output(text)
        self.assertTrue(len(result.body) > 50)
        self.assertEqual(len(result.titles), 5)
        self.assertEqual(result.titles[0], "标题一")
        self.assertEqual(result.digest, "这是阅读全文后形成的核心概括。")

    def test_parse_titles(self) -> None:
        result = parse_title_output('{"titles":["爆款1","爆款2","爆款3"]}')
        self.assertEqual(result.titles, ["爆款1", "爆款2", "爆款3"])

    def test_parse_titles_subtitles_without_body(self) -> None:
        """标题生成阶段常只返回 titles/subtitles JSON，不应被丢弃。"""
        result = parse_rewrite_output(
            '{"titles":["主标题甲","主标题乙"],"subtitles":["副题甲","副题乙","副题丙"]}'
        )
        self.assertEqual(result.body, "")
        self.assertEqual(result.titles, ["主标题甲", "主标题乙"])
        self.assertEqual(result.subtitles, ["副题甲", "副题乙", "副题丙"])

    def test_quality_similar_fails(self) -> None:
        raw = "同一段内容" * 80
        result = parse_rewrite_output(
            '{"body":"' + raw + '","titles":["t1"],"subtitles":["s1"]}'
        )
        with self.assertRaises(ValueError):
            quality_check(result, raw, min_body_chars=10, max_similarity=0.5)

    def test_quality_rejects_body_under_2000_when_hard_limit_used(self) -> None:
        result = parse_rewrite_output(
            '{"body":"' + ("正文内容" * 499) + '","titles":["合格标题示例"]}'
        )
        with self.assertRaisesRegex(ValueError, "正文不足 2000 字"):
            quality_check(result, "完全不同的参考材料", min_body_chars=2000)


class AdCoverRenderTests(unittest.TestCase):
    def test_stale_jobs_are_marked_cancelled_on_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "stale.db")
            stale_id = db.create_job(topic="旧任务")
            recent_id = db.create_job(topic="新任务")
            old = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(
                microsecond=0
            ).isoformat()
            with db.connect() as conn:
                conn.execute(
                    "UPDATE jobs SET status='rewriting', updated_at=? WHERE id=?",
                    (old, stale_id),
                )
                conn.execute(
                    "UPDATE jobs SET status='rewriting' WHERE id=?", (recent_id,)
                )
            self.assertEqual(db.recover_stale_jobs(older_than_minutes=30), 1)
            self.assertEqual(db.get_job(stale_id)["status"], "cancelled")
            self.assertIn("历史任务已中断", db.get_job(stale_id)["error"])
            self.assertEqual(db.get_job(recent_id)["status"], "rewriting")

    def test_ad_priority_and_rotate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "t.db")
            now = datetime.now(timezone.utc)
            db.upsert_ad(
                {
                    "id": "later",
                    "title": "Later",
                    "priority": 10,
                    "course_start_at": (now + timedelta(days=10)).isoformat(),
                    "enabled": True,
                }
            )
            db.upsert_ad(
                {
                    "id": "soon",
                    "title": "Soon",
                    "priority": 1,
                    "course_start_at": (now + timedelta(days=1)).isoformat(),
                    "enabled": True,
                }
            )
            chosen = select_ad(db)
            assert chosen is not None
            self.assertEqual(chosen["id"], "soon")
            html = render_ad_html(chosen)
            self.assertIn("Soon", html)

    def test_cover_keyword_map(self) -> None:
        cfg = {
            "cover": {
                "from_material_library": False,
                "default_media_id": "DEFAULT",
                "keyword_map": {"人工智能": "AI_COVER", "教育": "EDU_COVER"},
            }
        }
        self.assertEqual(resolve_cover(topic="谈谈人工智能趋势", config=cfg), "AI_COVER")
        self.assertEqual(resolve_cover(topic="无关话题", config=cfg), "DEFAULT")
        self.assertEqual(
            resolve_cover(topic="x", config=cfg, override_media_id="X"),
            "X",
        )

    def test_cover_random_from_library(self) -> None:
        cfg = {"cover": {"from_material_library": True, "default_media_id": ""}}
        picked = resolve_cover(
            topic="随便",
            config=cfg,
            pick_from_library=lambda: "LIB_RANDOM_1",
        )
        self.assertEqual(picked, "LIB_RANDOM_1")

    def test_cover_uses_account_fallback_when_library_temporarily_disconnects(self) -> None:
        cfg = {"cover": {"from_material_library": True, "default_media_id": ""}}

        def disconnected() -> str:
            raise ConnectionResetError(10054, "connection reset")

        picked = resolve_cover(
            topic="随便",
            config=cfg,
            pick_from_library=disconnected,
            fallback_media_id="LAST_ACCOUNT_COVER",
        )
        self.assertEqual(picked, "LAST_ACCOUNT_COVER")


    def test_renderer(self) -> None:
        cfg = load_config()
        html = TemplateRenderer(cfg).render(
            body="第一段\n\n## 小标题\n\n第二段",
            subtitle="副标题",
            ad_html="<p>广告</p>",
        )
        self.assertIn("第一段", html)
        self.assertIn("小标题", html)
        self.assertNotIn("默认课程推荐", html)
        self.assertNotIn("欢迎关注本公众号", html)
        self.assertTrue(len(make_digest("摘要内容测试")) > 0)

    def test_digest_fallback_samples_the_whole_article_within_wechat_limit(self) -> None:
        body = (
            "开头说明企业正在面对新的经营压力。\n\n"
            "第二段补充背景，但不是全文的最终结论。\n\n"
            "中段指出真正的关键是组织协作与决策效率。\n\n"
            "随后给出流程调整和责任边界的具体方法。\n\n"
            "结尾总结企业应把短期应对转化为长期能力建设。"
        )
        digest = make_digest(body)
        self.assertLessEqual(len(digest), 120)
        self.assertIn("组织协作与决策效率", digest)
        self.assertIn("长期能力建设", digest)
        self.assertNotEqual(digest, "".join(body.split())[:120])

    def test_renderer_separates_heading_from_body_and_hides_outline_labels(self) -> None:
        cfg = load_config()
        html = TemplateRenderer(cfg).render(
            body=(
                "## 开头钩子\n"
                "这是开场正文，不应被渲染成粗体标题。\n\n"
                "## 分论点1：组织能力决定执行上限\n"
                "第一段解释组织能力为什么重要。\n\n"
                "第二段用案例继续说明这个观点。"
            )
        )
        self.assertNotIn("开头钩子", html)
        self.assertNotIn("分论点", html)
        self.assertIn("组织能力决定执行上限", html)
        self.assertNotIn("一、", html)
        self.assertNotIn("<h2", html)
        self.assertIn("color:#595959;font-weight:bold", html)
        self.assertIn("line-height:35px", html)
        self.assertIn("margin:0 0 16px", html)
        self.assertIn("padding-right:10px;padding-left:10px", html)
        self.assertIn("蓝血创作组", html)
        self.assertIn("#ff6827", html)
        self.assertNotIn("#0052ff", html)
        self.assertNotIn("height:1px", html)
        self.assertIn("<p style=", html)
        self.assertIn("这是开场正文，不应被渲染成粗体标题。", html)

    def test_renderer_handles_double_escaped_manus_linebreaks(self) -> None:
        cfg = load_config()
        html = TemplateRenderer(cfg).render(
            body=r"开场正文。\n\n## 组织能力决定执行上限\n\n这里是论点说明。"
        )
        self.assertIn("开场正文。", html)
        self.assertIn("color:#595959;font-weight:bold", html)
        self.assertIn("组织能力决定执行上限", html)
        self.assertIn("这里是论点说明。", html)
        self.assertNotIn(r"\n", html)

    def test_legacy_default_blue_argument_inherits_body_color(self) -> None:
        layout = normalize_layout(
            {
                "body": {"color": "#f26b1d"},
                "argument": {
                    "font_size": "17px",
                    "color": "#0052ff",
                    "line_height": "1.8",
                    "spacing_before": "20px",
                    "spacing_after": "12px",
                    "alignment": "left",
                    "bold": True,
                    "background": "transparent",
                    "border_color": "transparent",
                },
            }
        )

        self.assertEqual(layout["argument"]["color"], "#f26b1d")

    def test_renderer_applies_custom_layout_and_list_styles(self) -> None:
        cfg = load_config()
        cfg["template"] = dict(cfg.get("template") or {})
        cfg["template"].update(
            {
                "paragraph_break_mode": "each_line",
                "body_font_size": "18px",
                "body_first_line_indent": "2em",
                "body_alignment": "justify",
                "argument_color": "#123456",
                "list_marker_color": "#654321",
            }
        )
        html = TemplateRenderer(cfg).render(
            body="第一行\n第二行\n\n## 核心论点\n\n- 无序项\n1. 有序项"
        )
        self.assertEqual(html.count("text-indent:2em !important"), 2)
        self.assertIn("font-size:18px;color:#595959", html)
        self.assertIn("text-align:justify", html)
        self.assertIn("color:#123456;font-weight:bold", html)
        self.assertIn("color:#654321;font-weight:bold", html)
        self.assertIn("无序项", html)
        self.assertIn("有序项", html)

    def test_renderer_converts_extended_markdown_without_raw_markers(self) -> None:
        cfg = load_config()
        html = TemplateRenderer(cfg).render(
            body=(
                "### 三级标题\n\n"
                "这里有 **加粗内容**、*强调内容* 和 [安全链接](https://example.com/a)。\n\n"
                "---\n\n"
                "| 项目 | 结果 |\n| --- | --- |\n| 模板 | 正常 |"
            )
        )
        self.assertIn("三级标题", html)
        self.assertIn("<strong>加粗内容</strong>", html)
        self.assertIn("<em>强调内容</em>", html)
        self.assertIn('href="https://example.com/a"', html)
        self.assertIn("<table", html)
        self.assertNotIn("**加粗内容**", html)
        self.assertNotIn("| --- |", html)

    def test_layout_validation_rejects_invalid_css(self) -> None:
        with self.assertRaisesRegex(ValueError, "排版参数不合法"):
            validate_layout(
                {
                    "body": {"font_size": "abc", "color": "not-a-color"},
                    "argument": {"spacing_before": "-20px"},
                }
            )

    def test_layout_validation_requires_image_agent_when_enabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须选择一个生图智能体"):
            validate_layout(
                {
                    "inline_images": {
                        "enabled": True,
                        "source_mode": "generate",
                        "image_model_id": "",
                    }
                }
            )

    def test_layout_validation_normalizes_image_concurrency(self) -> None:
        layout = validate_layout(
            {
                "inline_images": {
                    "enabled": True,
                    "source_mode": "generate",
                    "image_model_id": "image-agent-1",
                    "generation_concurrency": 99,
                }
            }
        )
        self.assertEqual(layout["inline_images"]["generation_concurrency"], 4)

    def test_layout_validation_requires_valid_benchmark_source_and_threshold(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "必须选择对标公众号"):
            validate_layout(
                {
                    "benchmark": {
                        "configured": True,
                        "enabled": True,
                        "source_account_id": "",
                    }
                }
            )
        with self.assertRaisesRegex(ValueError, "匹配阈值"):
            validate_layout(
                {
                    "benchmark": {
                        "configured": True,
                        "enabled": False,
                        "image_match_threshold": 1.2,
                    }
                }
            )


if __name__ == "__main__":
    unittest.main()
