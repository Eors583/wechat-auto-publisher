from urllib.parse import parse_qs, urlparse

from app.ui.desktop import (
    _preflight_repair_action,
    _preflight_repair_url,
)


def test_template_failure_routes_to_the_selected_accounts_template_manager() -> None:
    target = _preflight_repair_url("account-blueblood", "template")
    parsed = urlparse(target)

    assert parsed.path == "/"
    assert parse_qs(parsed.query) == {
        "view": ["config"],
        "repair": ["template"],
        "account_id": ["account-blueblood"],
    }
    assert _preflight_repair_action("template") == (
        "template",
        "打开模板管理",
    )


def test_preflight_failures_route_to_the_relevant_account_configuration() -> None:
    assert _preflight_repair_action("model")[0] == "account"
    assert _preflight_repair_action("wechat")[0] == "account"
    assert _preflight_repair_action("cover")[0] == "images"
    assert _preflight_repair_action("inline_images")[0] == "images"
    assert _preflight_repair_action("unknown")[0] == "account"
