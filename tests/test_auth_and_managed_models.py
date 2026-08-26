from __future__ import annotations

import textwrap

from fastapi.testclient import TestClient

from app.accounts import apply_account_selection, public_accounts, save_account
from app.ai.model_registry import GEMINI, OPENAI_COMPATIBLE, public_models, save_model
from app.api.server import create_api_app
from app.config import load_config
from app.db import Database
from app.db_backend import postgres_schema_sql, postgres_statement
from app.services.auth import AuthService
from app.services.batches import BatchService
from app.ui.state import AppState


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


def test_registration_grants_configured_signup_credits(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("WECHAT_PUBLISHER_SIGNUP_CREDITS", "1000")
    db = Database(tmp_path / "signup-credits.db")

    user = AuthService(db).register("welcome_user", "secret123")
    user_db = db.for_user(str(user["id"]))

    assert user_db.credit_wallet_summary()["available"] == 1000
    ledger = user_db.list_credit_ledger(limit=10)
    assert [(row["amount_points"], row["event_type"], row["reason"]) for row in ledger] == [
        (1000, "grant", "新用户体验积分")
    ]


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


def test_user_models_follow_login_account_and_keep_platform_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "test-only-user-model-ownership-key",
    )
    db = Database(tmp_path / "user-models.db")
    platform_db = db.for_user("")
    user_a_db = db.for_user("user-a")
    user_b_db = db.for_user("user-b")

    platform_id = save_model(
        platform_db,
        name="平台 Gemini",
        provider_type=GEMINI,
        api_base="",
        model="gemini-platform",
        api_key="platform-secret",
    )
    user_a_id = save_model(
        user_a_db,
        name="用户 A Gemini",
        provider_type=GEMINI,
        api_base="",
        model="gemini-user-a",
        api_key="user-a-secret",
    )
    user_b_id = save_model(
        user_b_db,
        name="用户 B Gemini",
        provider_type=GEMINI,
        api_base="",
        model="gemini-user-b",
        api_key="user-b-secret",
    )

    user_a_models = {item["id"]: item for item in public_models(user_a_db)}
    assert set(user_a_models) == {platform_id, user_a_id}
    assert user_a_models[platform_id]["scope"] == "platform"
    assert user_a_models[platform_id]["editable"] is False
    assert user_a_models[user_a_id]["scope"] == "private"
    assert user_a_models[user_a_id]["editable"] is True
    assert user_b_id not in user_a_models

    assert {item["id"] for item in public_models(db.for_user("user-a"))} == {
        platform_id,
        user_a_id,
    }
    assert {item["id"] for item in public_models(platform_db)} == {platform_id}

    try:
        save_model(
            user_b_db,
            model_id=user_a_id,
            name="越权修改",
            provider_type=GEMINI,
            api_base="",
            model="gemini-hijack",
            api_key="hijack-secret",
        )
    except ValueError as exc:
        assert "不属于当前登录账号" in str(exc)
    else:
        raise AssertionError("another user's model must not be editable")


def test_model_selector_labels_official_and_custom_and_protects_official_delete(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "test-only-model-selector-ownership-key",
    )
    root_db = Database(tmp_path / "model-selector.db")
    official_id = save_model(
        root_db.for_user(""),
        name="平台 Manus",
        provider_type=GEMINI,
        api_base="",
        model="manus-platform",
        api_key="platform-secret",
    )
    user_db = root_db.for_user("user-a")
    custom_id = save_model(
        user_db,
        name="我的模型",
        provider_type=GEMINI,
        api_base="",
        model="custom-model",
        api_key="user-secret",
    )

    ui_state = object.__new__(AppState)
    ui_state.db = user_db
    options = ui_state.model_options(include_default=False)

    assert options[official_id].startswith("官方 · API · ")
    assert options[custom_id].startswith("自定义 · API · ")

    try:
        user_db.delete_ai_model(official_id)
    except ValueError as exc:
        assert "不属于当前登录账号" in str(exc)
    else:
        raise AssertionError("official model deletion must be rejected")

    user_db.delete_ai_model(custom_id)
    assert user_db.get_ai_model(custom_id) is None


def test_private_model_rejects_insecure_or_internal_api_base(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "test-only-private-model-url-key",
    )
    user_db = Database(tmp_path / "private-model-url.db").for_user("user-a")

    try:
        save_model(
            user_db,
            name="不安全接口",
            provider_type=OPENAI_COMPATIBLE,
            api_base="http://api.example.com/v1",
            model="example-model",
            api_key="private-key",
        )
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("private model API Base must require HTTPS")

    try:
        save_model(
            user_db,
            name="内网接口",
            provider_type=OPENAI_COMPATIBLE,
            api_base="https://127.0.0.1/v1",
            model="example-model",
            api_key="private-key",
        )
    except ValueError as exc:
        assert "本机或内网" in str(exc)
    else:
        raise AssertionError("private model API Base must reject local targets")


def test_user_can_save_model_through_api_without_exposing_credentials(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "test-only-user-model-api-key",
    )
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "user-model-api.db"),
        "_db_target": str(tmp_path / "user-model-api.db"),
        "_data_dir": str(tmp_path / "data"),
        "auth": {"required": True},
        "api": {"token": ""},
        "ai": {},
        "feishu": {"enabled": False},
        "wechat": {},
    }
    app = create_api_app(
        config,
        BatchService(config),
        start_feishu=False,
    )

    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/login",
            json={"username": "lanxue", "password": "lanxue"},
        ).json()
        admin_headers = {"Authorization": f"Bearer {admin['token']}"}
        shared = client.post(
            "/api/v1/admin/models",
            headers=admin_headers,
            json={
                "name": "平台 Gemini",
                "provider_type": "gemini",
                "api_base": "",
                "model": "gemini-platform",
                "api_key": "platform-api-key",
                "enabled": True,
            },
        )
        assert shared.status_code == 200
        shared_model_id = str(shared.json()["id"])

        first = client.post(
            "/api/v1/auth/register",
            json={"username": "model_owner", "password": "secret123"},
        ).json()
        second = client.post(
            "/api/v1/auth/register",
            json={"username": "model_other", "password": "secret123"},
        ).json()
        first_headers = {"Authorization": f"Bearer {first['token']}"}
        second_headers = {"Authorization": f"Bearer {second['token']}"}

        reserved_id = client.post(
            "/api/v1/models",
            headers=first_headers,
            json={
                "id": "config:reserved",
                "name": "冲突模型",
                "provider_type": "gemini",
                "api_base": "",
                "model": "gemini-2.5-flash",
                "api_key": "private-user-key",
                "enabled": True,
            },
        )
        assert reserved_id.status_code == 400

        saved = client.post(
            "/api/v1/models",
            headers=first_headers,
            json={
                "name": "我的 Gemini",
                "provider_type": "gemini",
                "api_base": "",
                "model": "gemini-2.5-flash",
                "api_key": "private-user-key",
                "enabled": True,
            },
        )
        assert saved.status_code == 200
        model_id = str(saved.json()["id"])
        assert "private-user-key" not in saved.text
        assert saved.json()["scope"] == "private"
        assert saved.json()["editable"] is True

        first_models = client.get(
            "/api/v1/models",
            headers=first_headers,
        ).json()
        second_models = client.get(
            "/api/v1/models",
            headers=second_headers,
        ).json()
        first_by_id = {str(item["id"]): item for item in first_models}
        assert model_id in {str(item["id"]) for item in first_models}
        assert model_id not in {str(item["id"]) for item in second_models}
        assert first_by_id[shared_model_id]["scope"] == "platform"
        assert first_by_id[shared_model_id]["editable"] is False
        assert shared_model_id in {str(item["id"]) for item in second_models}

        local_saved = client.post(
            "/api/v1/models",
            headers=first_headers,
            json={
                "name": "我的本地 Ollama",
                "provider_type": "local_openai_compatible",
                "api_base": "http://localhost:11434/v1",
                "model": "qwen2.5:7b",
                "api_key": None,
                "enabled": True,
            },
        )
        assert local_saved.status_code == 200
        assert local_saved.json()["connection_type"] == "local"
        assert local_saved.json()["has_api_key"] is False
        local_model_id = str(local_saved.json()["id"])
        assert local_model_id not in {
            str(item["id"])
            for item in client.get(
                "/api/v1/models",
                headers=second_headers,
            ).json()
        }

        deleted = client.delete(
            f"/api/v1/models/{model_id}",
            headers=first_headers,
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert client.delete(
            f"/api/v1/models/{local_model_id}",
            headers=first_headers,
        ).status_code == 200


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
    assert "owner_user_id TEXT NOT NULL DEFAULT ''" in postgres_schema_sql(
        "owner_user_id TEXT NOT NULL DEFAULT ''"
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
