from __future__ import annotations

import inspect

from app.admin import server as admin_server
from app.ui import desktop
from app.ui.panels import billing
from app.ui.styles import APP_CSS


def test_customer_and_admin_have_separate_usage_entries() -> None:
    desktop_source = inspect.getsource(desktop.create_desktop_app)
    admin_source = inspect.getsource(admin_server.create_admin_app)

    assert 'ui.tab("套餐与用量", icon="toll")' in desktop_source
    assert "build_billing_panel(page_state)" in desktop_source
    assert 'ui.tab("AI 成本", icon="query_stats")' in admin_source
    assert "build_admin_billing_panel(state)" in admin_source


def test_customer_projection_never_renders_internal_cost_fields() -> None:
    source = inspect.getsource(billing.build_billing_panel)

    assert "provider_cost_micro_cny" not in source
    assert "retail_cost_micro_cny" not in source
    assert "API Key" in source


def test_billing_layout_has_explicit_scroll_and_long_content_containment() -> None:
    billing_css = APP_CSS[APP_CSS.index(".ops-billing-page .ops-page-host") :]

    assert "overflow-y: auto" in billing_css[:500]
    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in billing_css
    assert "overflow-wrap: anywhere" in billing_css
    assert ".ops-billing-table .q-table__middle" in billing_css
    assert "overflow-x: auto" in billing_css
    assert "@media (max-width: 600px)" in billing_css


def test_generation_completion_does_not_interrupt_with_a_usage_receipt() -> None:
    wizard_source = inspect.getsource(desktop._build_wizard)  # noqa: SLF001

    assert not hasattr(billing, "show_generation_usage_receipt")
    assert "generation_receipt" not in wizard_source
    assert "show_generation_usage_receipt" not in wizard_source
