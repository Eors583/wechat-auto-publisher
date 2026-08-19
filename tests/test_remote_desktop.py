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

    assert (
        '#define MyRemoteUrl "https://api.bluebloodlab.cn/publisher/"'
        in installer
    )
    assert installer.count(
        'Parameters: "--remote-url {#MyRemoteUrl}"'
    ) == 3
    assert "--local-agent --open-setup" in installer
    assert "BlueBloodLabCockpitBridge" in installer
    assert "procedure CurUninstallStepChanged" in installer
    assert "RegDeleteValue(" in installer

    build_script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "build_installer.ps1"
    ).read_text(encoding="utf-8")
    assert "$productionRemoteUrl = 'https://api.bluebloodlab.cn/publisher/'" in build_script
    assert "Public release remote URL must be exactly" in build_script


def test_portable_bridge_public_build_requires_valid_code_signing() -> None:
    build_script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "build_local_bridge.ps1"
    ).read_text(encoding="utf-8")

    assert "[switch]$PublicRelease" in build_script
    assert "WECHAT_PUBLISHER_SIGNING_THUMBPRINT" in build_script
    assert "signtool.exe" in build_script
    assert "Get-AuthenticodeSignature" in build_script
    assert "Status -ne 'Valid'" in build_script
    assert "$downloadExe = Join-Path $installerDir \"$exeName.exe\"" in build_script


def test_ui_smoke_server_enables_nicegui_user_storage(monkeypatch) -> None:
    from nicegui import ui

    received: dict[str, object] = {}
    monkeypatch.setattr(ui, "run", lambda **kwargs: received.update(kwargs))
    monkeypatch.setattr(launcher, "_configure_file_logging", lambda _name: None)

    launcher._run_ui_smoke_server(18991)

    assert received["port"] == 18991
    assert received["show"] is False
    assert str(received["storage_secret"]).startswith("package-smoke-")
