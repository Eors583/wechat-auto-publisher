from __future__ import annotations

from app.db import Database
from app.feishu.settings import (
    effective_feishu_settings,
    public_feishu_settings,
    save_feishu_settings,
)


def test_feishu_secrets_are_encrypted_and_can_be_preserved(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    save_feishu_settings(
        db,
        enabled=True,
        app_id="cli_test",
        app_secret="secret-value",
        verification_token="verify-value",
        encrypt_key="encrypt-value",
        allowed_open_ids=["ou_1"],
        default_account_ids=["account-1"],
        agent_model_id="config:moonshot",
    )

    raw = db.get_setting("feishu_integration") or ""
    assert "secret-value" not in raw
    assert "verify-value" not in raw
    assert "encrypt-value" not in raw
    public = public_feishu_settings(db)
    assert public["app_id"] == "cli_test"
    assert public["has_app_secret"] is True
    assert public["agent_model_id"] == "config:moonshot"
    assert "app_secret" not in public

    effective = effective_feishu_settings(db)
    assert effective["app_secret"] == "secret-value"
    assert effective["verification_token"] == "verify-value"
    assert effective["encrypt_key"] == "encrypt-value"

    save_feishu_settings(
        db,
        enabled=True,
        app_id="cli_changed",
        allowed_open_ids=["ou_2"],
    )
    effective = effective_feishu_settings(db)
    assert effective["app_id"] == "cli_changed"
    assert effective["app_secret"] == "secret-value"
    assert effective["verification_token"] == "verify-value"
    assert effective["encrypt_key"] == "encrypt-value"
    assert effective["allowed_open_ids"] == ["ou_2"]
    assert effective["agent_model_id"] == "config:moonshot"


def test_long_connection_save_can_clear_old_event_security(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    save_feishu_settings(
        db,
        enabled=True,
        app_id="cli_test",
        app_secret="secret-value",
        verification_token="obsolete-verification-token",
        encrypt_key="obsolete-encrypt-key",
        allow_all=True,
    )

    save_feishu_settings(
        db,
        enabled=True,
        app_id="cli_test",
        app_secret=None,
        clear_event_security=True,
        allow_all=True,
    )

    public = public_feishu_settings(db)
    assert public["has_app_secret"] is True
    assert public["has_verification_token"] is False
    assert public["has_encrypt_key"] is False
    effective = effective_feishu_settings(db)
    assert effective["app_secret"] == "secret-value"
    assert effective["verification_token"] == ""
    assert effective["encrypt_key"] == ""


def test_enabled_feishu_requires_app_credentials(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    try:
        save_feishu_settings(db, enabled=True, app_id="")
    except ValueError as exc:
        assert "App ID" in str(exc)
    else:
        raise AssertionError("missing App ID should fail")


def test_legacy_config_moonshot_default_is_not_treated_as_user_selection(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")

    effective = effective_feishu_settings(
        db,
        {
            "enabled": True,
            "app_id": "cli_legacy",
            "app_secret": "legacy-secret",
            "agent_model_id": "config:moonshot",
        },
    )

    assert effective["enabled"] is False
    assert effective["agent_model_id"] == ""


def test_bot_context_round_trip(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    context = {
        "stage": "hot_topics_selected",
        "recent_hot_topics": [{"number": 1, "title": "企业管理", "url": "https://example.com"}],
    }
    db.set_bot_context("chat-1", context)
    assert db.get_bot_context("chat-1") == context
    assert db.get_bot_context("missing") == {}


def test_review_queue_round_trip(tmp_path) -> None:
    from app.feishu.session import FeishuSessionStore

    sessions = FeishuSessionStore(Database(tmp_path / "app.db"))
    batch = {
        "jobs": [
            {
                "id": 1,
                "account_name": "公众号A",
                "status": "ready_for_review",
            },
            {
                "id": 2,
                "account_name": "公众号B",
                "status": "ready_for_review",
            },
        ]
    }
    first = sessions.start_review("chat-1", batch)
    assert first == {"job_id": 1, "account_name": "公众号A"}
    assert sessions.current_review_job_id("chat-1") == 1

    state = sessions.mark_reviewed("chat-1", 1)
    assert state["next"] == {"job_id": 2, "account_name": "公众号B"}
    assert sessions.current_review_job_id("chat-1") == 2
    assert sessions.all_reviews_completed("chat-1") is False

    state = sessions.mark_reviewed("chat-1", 2)
    assert state["all_completed"] is True
    assert sessions.all_reviews_completed("chat-1") is True


def test_review_queue_sync_uses_shared_batch_review_state(tmp_path) -> None:
    from app.feishu.session import FeishuSessionStore

    sessions = FeishuSessionStore(Database(tmp_path / "app.db"))
    initial = {
        "jobs": [
            {
                "id": 1,
                "account_name": "公众号A",
                "status": "ready_for_review",
                "review_status": "confirmed",
            },
            {
                "id": 2,
                "account_name": "公众号B",
                "status": "ready_for_review",
                "review_status": "confirmed",
            },
        ]
    }
    assert sessions.start_review("chat-1", initial) is None
    assert sessions.all_reviews_completed("chat-1") is True

    changed_elsewhere = {
        "jobs": [
            initial["jobs"][0],
            {
                **initial["jobs"][1],
                "review_status": "viewed",
            },
        ]
    }
    current = sessions.sync_review("chat-1", changed_elsewhere)

    assert current == {"job_id": 2, "account_name": "公众号B"}
    assert sessions.all_reviews_completed("chat-1") is False
    assert sessions.current_review_job_id("chat-1") == 2
    assert sessions.unreviewed_items("chat-1") == [
        {"job_id": 2, "account_name": "公众号B"}
    ]


def test_review_queue_excludes_failed_and_cancelled_jobs(tmp_path) -> None:
    from app.feishu.session import FeishuSessionStore

    sessions = FeishuSessionStore(Database(tmp_path / "app.db"))
    batch = {
        "jobs": [
            {
                "id": 1,
                "account_name": "待审核公众号",
                "status": "ready_for_review",
                "review_status": "unviewed",
            },
            {
                "id": 2,
                "account_name": "写入中公众号",
                "status": "injecting",
                "review_status": "confirmed",
            },
            {
                "id": 3,
                "account_name": "已写入公众号",
                "status": "drafted",
            },
            {
                "id": 4,
                "account_name": "已发布公众号",
                "status": "published",
            },
            {
                "id": 5,
                "account_name": "失败公众号",
                "status": "failed",
                "review_status": "unviewed",
            },
            {
                "id": 6,
                "account_name": "已停止公众号",
                "status": "cancelled",
                "review_status": "unviewed",
            },
        ]
    }

    first = sessions.start_review("chat-1", batch)
    state = sessions.review_state("chat-1")

    assert first == {"job_id": 1, "account_name": "待审核公众号"}
    assert [item["job_id"] for item in state["queue"]] == [1, 2, 3, 4]
    assert state["reviewed_job_ids"] == [2, 3, 4]
    assert sessions.unreviewed_items("chat-1") == [
        {"job_id": 1, "account_name": "待审核公众号"}
    ]
