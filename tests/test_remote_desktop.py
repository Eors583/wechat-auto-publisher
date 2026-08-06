from __future__ import annotations

from pathlib import Path

import pytest

from app import launcher


def test_remote_url_can_be_selected_by_command_line() -> None:
    assert (
        launcher._remote_ui_url(
            ["--remote-url", "https://publisher.bluebloodlab.cn/"]
        )
        == "https://publisher.bluebloodlab.cn"
    )


def test_remote_url_can_use_an_explicit_ip_and_port() -> None:
    assert (
        launcher._remote_ui_url(
            ["--remote-url", "http://47.99.126.8:18775/"]
        )
        == "http://47.99.126.8:18775"
    )


def test_remote_url_can_be_selected_by_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "WECHAT_PUBLISHER_REMOTE_URL",
        "https://publisher.bluebloodlab.cn/",
    )
    assert (
        launcher._remote_ui_url([])
        == "https://publisher.bluebloodlab.cn"
    )


@pytest.mark.parametrize(
    "url",
    [
        "publisher.bluebloodlab.cn",
        "file:///tmp/index.html",
    ],
)
def test_remote_url_rejects_non_http_targets(url: str) -> None:
    with pytest.raises(ValueError, match="HTTP"):
        launcher._remote_ui_url(["--remote-url", url])


def test_installer_shortcuts_open_the_hosted_application() -> None:
    installer = (
        Path(__file__).resolve().parents[1]
        / "packaging"
        / "公众号改写助手.iss"
    ).read_text(encoding="utf-8")

    assert '#define MyRemoteUrl "http://47.99.126.8/"' in installer
    assert installer.count(
        'Parameters: "--remote-url {#MyRemoteUrl}"'
    ) == 3


def test_ui_smoke_server_enables_nicegui_user_storage(monkeypatch) -> None:
    from nicegui import ui

    received: dict[str, object] = {}
    monkeypatch.setattr(ui, "run", lambda **kwargs: received.update(kwargs))
    monkeypatch.setattr(launcher, "_configure_file_logging", lambda _name: None)

    launcher._run_ui_smoke_server(18991)

    assert received["port"] == 18991
    assert received["show"] is False
    assert str(received["storage_secret"]).startswith("package-smoke-")
