from __future__ import annotations

from pathlib import Path

from app.ui.styles import APP_CSS


ROOT = Path(__file__).resolve().parents[1]


def test_content_source_selector_uses_readable_responsive_grid() -> None:
    desktop_source = (ROOT / "app" / "ui" / "desktop.py").read_text(
        encoding="utf-8"
    )

    assert '.classes("source-mode-toggle")' in desktop_source
    assert '"link": "文章链接"' in desktop_source
    assert '"references": "多篇参考"' in desktop_source
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in APP_CSS
    assert "@media (max-width: 420px)" in APP_CSS


def test_pasted_body_supports_resize_and_fullscreen_editing() -> None:
    desktop_source = (ROOT / "app" / "ui" / "desktop.py").read_text(
        encoding="utf-8"
    )

    assert '.classes("w-full article-body-input")' in desktop_source
    assert '.props("rows=12 outlined")' in desktop_source
    assert 'text_in.on("dblclick", open_body_editor)' in desktop_source
    assert '"放大编辑"' in desktop_source
    assert '"应用正文"' in desktop_source
    assert "resize: vertical !important" in APP_CSS
    assert "fullscreen-editor-card" in APP_CSS
