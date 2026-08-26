from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_subpath_is_available_on_api_certificate_host() -> None:
    compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
    nginx = (
        ROOT / "deploy" / "production" / "nginx.conf.example"
    ).read_text(encoding="utf-8")

    assert "WECHAT_PUBLISHER_ADMIN_ROOT_PATH: /admin" in compose
    api_host = nginx.split("server_name api.bluebloodlab.cn;", 1)[1].split(
        "server {", 1
    )[0]
    assert "location /admin/" in api_host
    assert "proxy_pass http://127.0.0.1:18777/;" in api_host
    assert "X-Forwarded-Prefix" not in nginx


def test_https_publisher_reuses_the_existing_api_certificate_host() -> None:
    compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
    nginx = (
        ROOT / "deploy" / "production" / "nginx.conf.example"
    ).read_text(encoding="utf-8")
    deploy = (
        ROOT / "deploy" / "production" / "deploy-from-git.sh"
    ).read_text(encoding="utf-8")

    assert "WECHAT_PUBLISHER_UI_ROOT_PATH: /publisher" in compose
    assert '"18778:18765"' in compose
    assert "location /publisher/" in nginx
    assert "proxy_pass http://127.0.0.1:18778/;" in nginx
    assert "http://127.0.0.1:18778/publisher/" in deploy


def test_cockpit_bridge_download_is_an_exact_non_browsable_file_route() -> None:
    nginx = (
        ROOT / "deploy" / "production" / "nginx.conf.example"
    ).read_text(encoding="utf-8")

    assert (
        "location = /downloads/BlueBloodLab-Cockpit-Bridge-1.4.2.exe"
        in nginx
    )
    assert (
        "alias /opt/wechat-publisher/shared/downloads/"
        "BlueBloodLab-Cockpit-Bridge-1.4.2.exe;"
        in nginx
    )
    assert "default_type application/octet-stream;" in nginx
    assert "autoindex on" not in nginx


def test_production_processes_share_one_release_lease_owner() -> None:
    compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")

    assert "WECHAT_PUBLISHER_LAUNCH_SESSION_ID: ${APP_VERSION:-production}" in compose
