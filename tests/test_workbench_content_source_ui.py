from __future__ import annotations

from pathlib import Path

from app.ui.styles import APP_CSS


ROOT = Path(__file__).resolve().parents[1]


def test_content_source_selector_uses_readable_responsive_grid() -> None:
    desktop_source = (ROOT / "app" / "ui" / "desktop.py").read_text(
        encoding="utf-8"
    )

    assert '.classes("source-mode-toggle ops-segment")' in desktop_source
    assert '"link": "文章链接"' in desktop_source
    assert '"references": "多篇参考"' in desktop_source
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in APP_CSS
    assert "@media (max-width: 420px)" in APP_CSS


def test_pasted_body_supports_resize_and_fullscreen_editing() -> None:
    desktop_source = (ROOT / "app" / "ui" / "desktop.py").read_text(
        encoding="utf-8"
    )

    assert '"w-full article-body-input"' in desktop_source
    assert '.props("rows=8 outlined")' in desktop_source
    assert 'text_in.on("dblclick", open_body_editor)' in desktop_source
    assert '"放大编辑"' in desktop_source
    assert '"应用正文"' in desktop_source
    assert "resize: vertical !important" in APP_CSS
    assert "fullscreen-editor-card" in APP_CSS


def test_topic_is_optional_for_every_source_mode() -> None:
    desktop_source = (ROOT / "app" / "ui" / "desktop.py").read_text(
        encoding="utf-8"
    )

    assert 'ui.input(\n                "文章主题（可选）"' in desktop_source
    assert 'if source_mode_value == "topic" and not topic:' in desktop_source
    assert 'topic = "由 AI 自动策划选题"' in desktop_source
    assert 'ui.notify("话题原创模式请填写文章主题"' not in desktop_source
    assert 'if not topic:\n                ui.notify("请先选择或输入话题"' not in desktop_source
    assert 'if mode == "link":\n                return bool(str(url_in.value' in desktop_source
    assert 'if mode == "text":\n                return bool(str(text_in.value' in desktop_source


def test_wechat_links_are_extracted_by_the_user_local_bridge_before_batch_creation() -> None:
    desktop_source = (ROOT / "app" / "ui" / "desktop.py").read_text(
        encoding="utf-8"
    )

    assert "http://127.0.0.1:11798/extract/wechat" in desktop_source
    assert "正在通过本机助手获取公众号正文" in desktop_source
    assert "本机获取未成功，正在自动切换服务器解析" in desktop_source
    assert "已自动切换服务器多级解析，不中断当前生成任务。" in desktop_source
    assert "source_mode_value = \"text\"" in desktop_source
    assert "reference_urls = []" in desktop_source
    assert "raw_content=text or None" in desktop_source
    assert "targetAddressSpace" not in desktop_source
