from __future__ import annotations

import textwrap

from fastapi.testclient import TestClient

from app.accounts import apply_account_selection, public_accounts, save_account
from app.ai.model_registry import OPENAI_COMPATIBLE, save_model
from app.api.server import create_api_app
from app.config import load_config
from app.db import Database
from app.db_backend import postgres_schema_sql, postgres_statement
from app.services.auth import AuthService
from app.services.batches import BatchService


def test_default_admin_registration_and_persisted_login(tmp_path) -> None:
    db = Database(tmp_path / "auth.db")
    service = AuthService(db)

    admin = service.ensure_default_admin()
    assert admin["username"] == "lanxue"
    assert admin["role"] == "admin"
    assert "password_hash" not in admin

    user = service.register("operator_01", "secret123")
    assert user["role"] == "user"
    login = service.login("operator_01", "secret123")
    assert service.authenticate(login["token"])["id"] == user["id"]

    service.logout(login["token"])
    assert service.authenticate(login["token"]) is None


def test_auth_api_and_admin_model_boundary(tmp_path) -> None:
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "api-auth.db"),
        "_db_target": str(tmp_path / "api-auth.db"),
        "_data_dir": str(tmp_path / "data"),
        "auth": {"required": True},
        "api": {"token": ""},
        "ai": {},
        "feishu": {"enabled": False},
        "wechat": {},
    }
    service = BatchService(config)
    app = create_api_app(config, service, start_feishu=False)

    with TestClient(app) as client:
        assert client.get("/api/v1/accounts").status_code == 401
        admin_login = client.post(
            "/api/v1/auth/login",
            json={"username": "lanxue", "password": "lanxue"},
        )
        assert admin_login.status_code == 200
        admin_header = {
            "Authorization": f"Bearer {admin_login.json()['token']}"
        }
        assert client.get(
            "/api/v1/admin/models",
            headers=admin_header,
        ).status_code == 200

        user_login = client.post(
            "/api/v1/auth/register",
            json={"username": "normal_user", "password": "secret123"},
        )
        assert user_login.status_code == 200
        user_header = {
            "Authorization": f"Bearer {user_login.json()['token']}"
        }
        assert client.get(
            "/api/v1/models",
            headers=user_header,
        ).status_code == 200
        assert client.get(
            "/api/v1/admin/models",
            headers=user_header,
        ).status_code == 403


def test_auth_required_cannot_be_disabled_by_legacy_environment(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            data_dir: data
            db:
              path: data/test.db
            auth:
              required: false
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTH_REQUIRED", "false")

    config = load_config(config_path)

    assert config["auth"]["required"] is True


def test_unbound_account_uses_only_explicit_merchant_default(tmp_path) -> None:
    db = Database(tmp_path / "managed-model.db")
    model_id = save_model(
        db,
        name="商户 Kimi",
        provider_type=OPENAI_COMPATIBLE,
        api_base="https://example.test/v1",
        model="moonshot-v1",
        api_key="merchant-secret",
        enabled=True,
    )
    account_id = save_account(
        db,
        name="测试公众号",
        app_id="wx-managed-model",
        app_secret="wechat-secret",
        model_id="",
    )
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "managed-model.db"),
        "_data_dir": str(tmp_path / "data"),
        "ai": {},
        "wechat": {},
    }

    assert public_accounts(db)[0]["has_model"] is False
    db.set_setting("merchant.default_text_model_id", model_id)
    account = public_accounts(db)[0]
    assert account["has_model"] is True
    assert account["uses_platform_default_model"] is True

    effective, selected = apply_account_selection(config, db, account_id)
    assert selected["_effective_model_id"] == model_id
    assert effective["ai"]["primary"] == model_id


def test_postgres_sql_translation_keeps_repository_contract() -> None:
    statement, lastrowid = postgres_statement(
        "INSERT OR IGNORE INTO draft_deliveries (idempotency_key) VALUES (?)"
    )
    assert statement.endswith("ON CONFLICT DO NOTHING")
    assert "%s" in statement
    assert lastrowid is False

    statement, lastrowid = postgres_statement(
        "INSERT INTO jobs (created_at) VALUES (?)"
    )
    assert statement.endswith("RETURNING id")
    assert lastrowid is True
    assert "BIGSERIAL PRIMARY KEY" in postgres_schema_sql(
        "id INTEGER PRIMARY KEY AUTOINCREMENT"
    )

    statement, lastrowid = postgres_statement(
        "DELETE FROM followed_articles WHERE url LIKE 'https://mp.weixin.qq.com/%'"
    )
    assert "mp.weixin.qq.com/%%" in statement
    assert lastrowid is False


def test_postgres_null_safe_job_revision_comparison_is_valid() -> None:
    statement, lastrowid = postgres_statement(
        """
        UPDATE jobs
        SET body = ?,
            content_revision = content_revision
                + CASE WHEN body IS DISTINCT FROM ? THEN 1 ELSE 0 END
        WHERE id = ?
        """
    )

    assert "body IS DISTINCT FROM %s" in statement
    assert "IS NOT %s" not in statement
    assert lastrowid is False
