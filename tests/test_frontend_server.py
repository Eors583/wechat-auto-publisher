from __future__ import annotations

from fastapi.testclient import TestClient

from app.frontend.server import create_frontend_app


def test_vue_frontend_server_health_assets_and_spa_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>Element Plus</title><div id='app'></div>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("window.elementApp = true", encoding="utf-8")
    monkeypatch.setenv("WECHAT_PUBLISHER_FRONTEND_DIST", str(dist))

    with TestClient(create_frontend_app()) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["frontend"] == "vue-element-plus"
        assert "Element Plus" in client.get("/").text
        assert "Element Plus" in client.get("/tasks/deep-link").text
        assert client.get("/assets/app.js").text == "window.elementApp = true"
        assert client.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"


def test_vue_frontend_server_reports_missing_build(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WECHAT_PUBLISHER_FRONTEND_DIST", str(tmp_path / "missing"))
    with TestClient(create_frontend_app()) as client:
        assert client.get("/health").status_code == 503
        response = client.get("/")
        assert response.status_code == 503
        assert response.json()["detail"] == "前端资源尚未构建"
