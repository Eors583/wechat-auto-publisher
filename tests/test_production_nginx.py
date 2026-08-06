from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_element_admin_spa_uses_stripped_subpath_and_dedicated_port() -> None:
    compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
    nginx = (
        ROOT / "deploy" / "production" / "nginx.conf.example"
    ).read_text(encoding="utf-8")

    assert compose.count("app.frontend.server") == 2
    assert 'WECHAT_PUBLISHER_FRONTEND_PORT: "18767"' in compose
    assert "location /admin/" in nginx
    assert "proxy_pass http://127.0.0.1:18777/;" in nginx
    assert "X-Forwarded-Prefix" not in nginx
