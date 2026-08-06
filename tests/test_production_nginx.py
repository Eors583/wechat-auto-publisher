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
