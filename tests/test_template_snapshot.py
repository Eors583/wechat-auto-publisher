from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.render import TemplateRenderer
from app.render.finalize import finalize_article_html, inspect_wechat_html
from app.wechat.template_snapshot import (
    list_template_draft_candidates,
    load_template_snapshot,
    merge_template_html,
    save_template_draft_candidate,
)


class TemplateSnapshotTests(unittest.TestCase):
    class _DraftClient:
        def __init__(self, rows):
            self.rows = rows

        def request(self, *_args, **_kwargs):
            return {"item": self.rows, "total_count": len(self.rows)}

    def test_snapshot_load_and_placeholder_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.html"
            path.write_text(
                '<section><p class="head">固定页眉</p>'
                '<section class="slot"><span>蓝血经营管理系统正文</span></section>'
                '<p class="foot">固定页尾</p></section>',
                encoding="utf-8",
            )
            snapshot = load_template_snapshot({"snapshot_path": str(path)})
            self.assertIsNotNone(snapshot)

            result = merge_template_html(snapshot.content, "<section><p>新正文</p></section>")
            self.assertIn("固定页眉", result)
            self.assertIn("固定页尾", result)
            self.assertIn("新正文", result)
            self.assertNotIn("蓝血经营管理系统正文", result)

    def test_final_preview_keeps_template_image_markup_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.html"
            path.write_text(
                '<section><p>固定页眉</p><img src="https://img.example/a.jpg" '
                'style="width:677px !important;height:320px !important">'
                '<p>蓝血经营管理系统正文</p><p>固定页尾</p></section>',
                encoding="utf-8",
            )
            result = finalize_article_html(
                "<section><p>生成正文</p></section>",
                {
                    "enabled": True,
                    "snapshot_path": str(path),
                    "placeholder": "蓝血经营管理系统正文",
                },
            )
            self.assertIsNotNone(result.snapshot)
            self.assertIn("固定页眉", result.html)
            self.assertIn("固定页尾", result.html)
            self.assertIn("生成正文", result.html)
            self.assertNotIn("蓝血经营管理系统正文", result.html)
            self.assertIn("width:677px !important", result.html)
            self.assertNotIn("max-width:100% !important", result.html)
            self.assertTrue(result.report.ok)
            self.assertEqual(result.report.image_count, 1)

    def test_zero_first_line_indent_survives_template_merge(self) -> None:
        root = Path(__file__).resolve().parents[1]
        generated = TemplateRenderer(
            {
                "_root": str(root),
                "template": {
                    "path": "app/render/templates/article.html.j2",
                    "body_first_line_indent": "0em",
                },
            }
        ).render(body="第一段正文。\n\n第二段正文。", show_byline=False)

        result = finalize_article_html(
            generated,
            {"enabled": True, "placeholder": "正文"},
            snapshot=type(
                "Snapshot",
                (),
                {"content": "<section><p>页眉</p><p>正文</p><p>页尾</p></section>"},
            )(),
            load_local_snapshot=False,
        )

        self.assertEqual(result.html.count("text-indent:0em"), 2)
        self.assertIn('class="article-preview"', result.html)
        self.assertIn("margin:0;padding:0;text-indent:0;", result.html)
        self.assertTrue(result.report.ok)

    def test_zero_first_line_indent_resets_inherited_template_indent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        generated = TemplateRenderer(
            {
                "_root": str(root),
                "template": {
                    "path": "app/render/templates/article.html.j2",
                    "body_first_line_indent": "0em",
                    "body_horizontal_padding": "0px",
                },
            }
        ).render(body="第一段正文。\n\n第二段正文。", show_byline=False)

        result = finalize_article_html(
            generated,
            {"enabled": True, "placeholder": "正文"},
            snapshot=type(
                "Snapshot",
                (),
                {
                    "content": (
                        '<section style="text-indent:2em">'
                        '<p>页眉</p><p>正文</p><p>页尾</p></section>'
                    )
                },
            )(),
            load_local_snapshot=False,
        )

        self.assertIn('<section style="text-indent:2em">', result.html)
        self.assertIn('class="article-preview"', result.html)
        self.assertIn("margin:0;padding:0;text-indent:0;", result.html)
        self.assertEqual(result.html.count("text-indent:0em"), 2)
        self.assertTrue(result.report.ok)

    def test_template_preserves_video_and_decorations_around_placeholder(self) -> None:
        template = (
            '<section><p>页眉</p>'
            '<section><iframe src="https://mp.weixin.qq.com/video"></iframe></section>'
            '<section><span>正文</span><span>▼▼▼</span></section>'
            '<p>页尾</p></section>'
        )
        result = finalize_article_html(
            "<section><p>生成后的完整正文</p></section>",
            {"enabled": True, "placeholder": "正文"},
            snapshot=type("Snapshot", (), {"content": template})(),
            load_local_snapshot=False,
        )
        self.assertIn("iframe", result.html.lower())
        self.assertIn("https://mp.weixin.qq.com/video", result.html)
        self.assertIn("▼", result.html)
        self.assertNotIn(">正文<", result.html)
        self.assertIn("生成后的完整正文", result.html)
        self.assertTrue(result.report.ok)

    def test_short_placeholder_keeps_media_link_and_profile_siblings(self) -> None:
        template = (
            '<section data-role="paragraph">'
            '<p><span>正文</span></p><p>▼ ▼ ▼</p>'
            '<p><a href="https://example.com/article"><img src="https://example.com/card.jpg"></a></p>'
            '<section class="mp_profile_iframe_wrp">'
            '<mp-common-profile data-nickname="品牌公众号"></mp-common-profile>'
            '</section></section>'
        )
        result = merge_template_html(
            template,
            '<section class="generated"><p>生成文章内容</p></section>',
            "正文",
        )
        self.assertIn("生成文章内容", result)
        self.assertNotIn(">正文<", result)
        self.assertIn("▼", result)
        self.assertIn("https://example.com/article", result)
        self.assertIn("https://example.com/card.jpg", result)
        self.assertIn("mp-common-profile", result)
        self.assertIn("品牌公众号", result)

    def test_quality_check_rejects_raw_markdown_and_missing_image_source(self) -> None:
        report = inspect_wechat_html("<p>**未渲染加粗**</p><img style='max-width:100% !important;height:auto !important'>")
        self.assertFalse(report.ok)
        self.assertTrue(any("Markdown" in item for item in report.errors))
        self.assertTrue(any("缺少" in item for item in report.errors))

    def test_template_draft_candidates_are_isolated_by_account_client(self) -> None:
        account_a = self._DraftClient(
            [
                {
                    "media_id": "media-a",
                    "content": {
                        "news_item": [
                            {"title": "品牌A模板一", "content": "<p>公众号正文</p>"},
                            {"title": "普通草稿", "content": "<p>不应出现</p>"},
                        ]
                    },
                }
            ]
        )
        account_b = self._DraftClient(
            [
                {
                    "media_id": "media-b",
                    "content": {
                        "news_item": [
                            {"title": "品牌B模板", "content": "<p>缺少占位符</p>"}
                        ]
                    },
                }
            ]
        )
        config = {"placeholder": "公众号正文", "scan_limit": 80}
        rows_a = list_template_draft_candidates(account_a, config)
        rows_b = list_template_draft_candidates(account_b, config)
        self.assertEqual([item.title for item in rows_a], ["品牌A模板一"])
        self.assertEqual([item.media_id for item in rows_a], ["media-a"])
        self.assertTrue(rows_a[0].has_placeholder)
        self.assertEqual([item.title for item in rows_b], ["品牌B模板"])
        self.assertFalse(rows_b[0].has_placeholder)
        self.assertNotEqual(rows_a[0].media_id, rows_b[0].media_id)

    def test_selected_template_candidate_is_saved_to_account_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "account-a.html"
            client = self._DraftClient(
                [
                    {
                        "media_id": "media-a",
                        "content": {
                            "news_item": [
                                {"title": "品牌模板", "content": "<section>公众号正文</section>"}
                            ]
                        },
                    }
                ]
            )
            config = {"placeholder": "公众号正文", "snapshot_path": str(path)}
            candidate = list_template_draft_candidates(client, config)[0]
            snapshot = save_template_draft_candidate(config, candidate)
            self.assertEqual(snapshot.source_media_id, "media-a")
            self.assertEqual(snapshot.source_title, "品牌模板")
            self.assertEqual(path.read_text(encoding="utf-8"), "<section>公众号正文</section>")

            with self.assertRaisesRegex(ValueError, "缺少正文占位符"):
                save_template_draft_candidate(
                    {"placeholder": "另一段替换字样", "snapshot_path": str(path)},
                    candidate,
                )


if __name__ == "__main__":
    unittest.main()
