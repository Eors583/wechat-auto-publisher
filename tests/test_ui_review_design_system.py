from __future__ import annotations

from pathlib import Path

from app.ui.styles import APP_CSS

ROOT = Path(__file__).resolve().parents[1]
TASKS_PANEL = ROOT / "app" / "ui" / "panels" / "tasks.py"
REVIEW_PANEL = ROOT / "app" / "ui" / "panels" / "review_jury.py"


def test_review_workbench_uses_named_quasar_component_surfaces() -> None:
    tasks_source = TASKS_PANEL.read_text(encoding="utf-8")
    review_source = REVIEW_PANEL.read_text(encoding="utf-8")

    for class_name in (
        "review-workbench",
        "review-workbench__header",
        "review-quick-summary",
        "review-body-editor",
    ):
        assert class_name in tasks_source

    for class_name in (
        "review-jury",
        "review-jury-intro",
        "review-settings",
        "review-surface",
        "review-score-card",
        "review-issue-card",
        "review-comparison-card",
        "review-risk-card",
        "review-choice-card",
        "review-progress-card",
    ):
        assert class_name in review_source


def test_review_design_system_is_responsive_and_token_driven() -> None:
    for selector in (
        ".review-workbench {",
        ".review-jury {",
        ".review-score-grid {",
        ".review-comparison-card {",
        ".review-issue-card--safety {",
        ".review-choice-card {",
        ".review-progress-card--completed {",
    ):
        assert selector in APP_CSS

    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in APP_CSS
    assert "@media (max-width: 980px)" in APP_CSS
    assert "@media (max-width: 620px)" in APP_CSS
