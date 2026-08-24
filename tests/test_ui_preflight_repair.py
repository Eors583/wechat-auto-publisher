from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.ui.preflight_repair import (
    preflight_failures,
    preflight_repair_action,
    preflight_repair_url,
)


def test_template_failure_routes_to_the_selected_accounts_template_manager() -> None:
    target = preflight_repair_url("account-blueblood", "template")
    parsed = urlparse(target)

    assert parsed.path == "/"
    assert parse_qs(parsed.query) == {
        "view": ["config"],
        "repair": ["template"],
        "account_id": ["account-blueblood"],
    }
    assert preflight_repair_action("template") == (
        "template",
        "打开模板管理",
    )


def test_preflight_failures_route_to_the_relevant_account_configuration() -> None:
    assert preflight_repair_action("model")[0] == "account"
    assert preflight_repair_action("wechat")[0] == "account"
    assert preflight_repair_action("cover")[0] == "images"
    assert preflight_repair_action("inline_images")[0] == "images"
    assert preflight_repair_action("unknown")[0] == "account"


def test_failed_preflight_reason_and_matching_repair_target_are_preserved() -> None:
    failures = preflight_failures(
        [
            {
                "account_id": "account-blueblood",
                "account_name": "蓝血研究",
                "checks": [
                    {
                        "key": "template",
                        "name": "本次文章模板与正文占位符",
                        "ok": False,
                        "message": "模板不存在或缺少正文占位符，请到模板管理重新同步",
                    },
                    {
                        "key": "draft",
                        "name": "草稿接口",
                        "ok": True,
                        "message": "草稿接口正常",
                    },
                ],
            }
        ]
    )

    assert failures == [
        {
            "account_id": "account-blueblood",
            "account_name": "蓝血研究",
            "check_key": "template",
            "check_name": "本次文章模板与正文占位符",
            "reason": "模板不存在或缺少正文占位符，请到模板管理重新同步",
            "repair_label": "打开模板管理",
            "repair_url": (
                "/?view=config&repair=template&account_id=account-blueblood"
            ),
        }
    ]


def test_operational_preflight_failures_use_the_actionable_dialog() -> None:
    desktop_source = Path("app/ui/desktop.py").read_text(encoding="utf-8")
    task_source = Path("app/ui/panels/tasks.py").read_text(encoding="utf-8")
    onboarding_source = Path("app/ui/panels/onboarding_wizard.py").read_text(
        encoding="utf-8"
    )
    repair_source = Path("app/ui/preflight_repair.py").read_text(encoding="utf-8")

    assert "当前仅可生成，暂不可写草稿" in desktop_source
    assert "show_preflight_repair_dialog" in desktop_source
    assert task_source.count("show_preflight_repair_dialog(") >= 3
    assert "当前配置无法写入公众号草稿" in task_source
    assert "preflight_repair_url(aid, key)" in onboarding_source
    assert "with client.content:" in repair_source
