from __future__ import annotations

import re
from pathlib import Path

from app.ui.style_tokens import UI_STYLE_SPEC, style_css_variables
from app.ui.styles import APP_CSS


ROOT = Path(__file__).resolve().parents[1]


def test_style_spec_is_the_single_source_for_global_css_variables() -> None:
    assert set(UI_STYLE_SPEC) == {"颜色", "字体", "间距", "圆角", "阴影", "布局"}
    names = [name for group in UI_STYLE_SPEC.values() for name in group]
    assert len(names) == len(set(names))

    variables = style_css_variables()
    for group in UI_STYLE_SPEC.values():
        for name, token in group.items():
            assert f"--ui-{name}: {token.value};" in variables
    assert APP_CSS.startswith(variables)


def test_migrated_component_styles_do_not_define_raw_palette_values() -> None:
    component_files = (
        "app/ui/panels/auth.py",
        "app/ui/panels/onboarding_wizard.py",
        "app/ui/panels/followed_articles.py",
        "app/ui/panels/review_jury.py",
        "app/ui/panels/tasks.py",
    )
    raw_color = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(")
    for relative_path in component_files:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert not raw_color.search(source), relative_path


def test_style_spec_document_explains_token_only_component_rules() -> None:
    document = (ROOT / "docs/ui-style-spec.md").read_text(encoding="utf-8")
    assert "UI_STYLE_SPEC" in document
    assert "不得在组件文件里新增十六进制色值" in document
    assert "var(--ui-space-6)" in document
