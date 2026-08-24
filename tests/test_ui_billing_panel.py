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


def test_generation_completion_opens_a_durable_usage_receipt() -> None:
    receipt_source = inspect.getsource(billing.show_generation_usage_receipt)
    wizard_source = inspect.getsource(desktop._build_wizard)  # noqa: SLF001

    assert '"文章生成完成"' in receipt_source
    assert '"本次生成已停止"' in receipt_source
    assert '"本次生成未完成"' in receipt_source
    assert "本次预计消耗" in receipt_source
    assert "本次积分待计价" in receipt_source
    assert "本次实际扣除" in receipt_source
    assert "输入 Token" in receipt_source
    assert "输出 Token" in receipt_source
    assert 'ui_root_url({"view": "billing"})' in receipt_source
    assert "dialog.open()" in receipt_source
    assert "generation_receipt" in wizard_source
    assert "show_generation_usage_receipt(" in wizard_source


def test_generation_receipt_contains_long_values_and_reflows_on_mobile() -> None:
    receipt_css = APP_CSS[APP_CSS.index(".ops-usage-receipt {") :]

    assert "overflow-x: hidden" in receipt_css[:400]
    assert "overflow-wrap: anywhere" in receipt_css
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in receipt_css
    assert ".ops-usage-receipt-actions .q-btn" in receipt_css
    mobile_css = receipt_css[receipt_css.index("@media (max-width: 600px)") :]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in mobile_css
    assert "width: 100%" in mobile_css
