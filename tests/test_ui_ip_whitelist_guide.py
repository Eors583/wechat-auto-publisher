from __future__ import annotations

import inspect
import json

from nicegui import ui

from app.ui import ip_whitelist_guide
from app.ui.panels import tasks


def _snapshot() -> str:
    return json.dumps(
        [
            {
                "type": type(element).__name__,
                "text": getattr(element, "text", None),
                "value": getattr(element, "value", None),
                "props": getattr(element, "_props", {}),
            }
            for element in ui.context.client.elements.values()
        ],
        ensure_ascii=False,
        default=str,
    )


def test_ip_whitelist_detector_accepts_structured_and_raw_wechat_errors() -> None:
    assert ip_whitelist_guide.has_ip_whitelist_issue(
        RuntimeError("WeChat API error 40164: invalid ip")
    )
    assert ip_whitelist_guide.has_ip_whitelist_issue(
        {
            "code": "inject.ip_not_whitelisted",
            "title": "微信 IP 白名单未放行",
        }
    )
    assert ip_whitelist_guide.has_ip_whitelist_issue(
        [{"checks": [{"message": "当前出口 IP 未加入白名单"}]}]
    )


def test_ip_whitelist_detector_does_not_match_generic_check_label() -> None:
    assert not ip_whitelist_guide.has_ip_whitelist_issue(
        {
            "label": "公众号凭证与 IP 白名单",
            "message": "AppSecret 无效或已重置",
        }
    )


def test_ip_whitelist_guide_shows_fixed_ip_and_operator_steps() -> None:
    try:
        ip_whitelist_guide.show_ip_whitelist_guide(["蓝血研究"])
        snapshot = _snapshot()
    finally:
        ui.context.client.remove_all_elements()

    assert "微信公众号 IP 白名单未配置" in snapshot
    assert "蓝血研究" in snapshot
    assert ip_whitelist_guide.FIXED_EGRESS_IP in snapshot
    assert "设置与开发" in snapshot
    assert "基本配置" in snapshot
    assert "复制 IP" in snapshot
    assert "打开微信公众平台" in snapshot


def test_batch_draft_write_checks_ip_before_injection() -> None:
    source = inspect.getsource(tasks.confirm_batch_write)

    assert "service.preflight(" in source
    assert "force_wechat_check=True" in source
    assert "has_ip_whitelist_issue(reports)" in source
    assert "show_ip_whitelist_guide(names)" in source
    assert source.index("service.preflight(") < source.index("service.inject_batch(")
