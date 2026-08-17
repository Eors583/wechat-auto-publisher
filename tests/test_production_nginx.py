from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_subpath_is_added_exactly_once() -> None:
    compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
    nginx = (
        ROOT / "deploy" / "production" / "nginx.conf.example"
    ).read_text(encoding="utf-8")

    assert "WECHAT_PUBLISHER_ADMIN_ROOT_PATH: /admin" in compose
    assert "location /admin/" in nginx
    assert "proxy_pass http://127.0.0.1:18777/;" in nginx
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
