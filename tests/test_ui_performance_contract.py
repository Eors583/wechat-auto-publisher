from __future__ import annotations

from app.ui.server import robots_txt
from app.ui.styles import APP_CSS, HEAD_HTML


def test_head_metadata_is_local_and_search_friendly() -> None:
    assert 'name="description"' in HEAD_HTML
    assert "document.documentElement.lang = 'zh-CN'" in HEAD_HTML
    assert "fonts.googleapis.com" not in HEAD_HTML
    assert "fonts.gstatic.com" not in HEAD_HTML


def test_lcp_containers_do_not_start_transparent() -> None:
    shell_rule = APP_CSS.split(".shell {", 1)[1].split("}", 1)[0]
    card_rule = APP_CSS.split(".card {", 1)[1].split("}", 1)[0]

    assert "animation:" not in shell_rule
    assert "animation:" not in card_rule


def test_robots_route_returns_valid_private_app_policy() -> None:
    response = robots_txt()

    assert response.media_type == "text/plain"
    assert response.body == b"User-agent: *\nDisallow: /\n"


def test_lazy_wizard_wrapper_preserves_the_responsive_two_column_layout() -> None:
    assert ".wizard-layout > .topic-card" in APP_CSS
    assert ".wizard-layout > .source-card" in APP_CSS
    assert ".wizard-layout > .action-card" in APP_CSS
    assert ".wizard-layout::after" in APP_CSS
    assert ".wizard-panel > .topic-card" not in APP_CSS
