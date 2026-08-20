from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.db import Database
from app.feishu.events import parse_message_event
from app.feishu.bot import FeishuBot
from app.feishu.session import FeishuSessionStore
from app.feishu.settings import public_feishu_settings
from app.feishu.tool_executor import FeishuToolExecutor
from app.services.auth import AuthService
from app.services.batches import BatchService
from app.services.feishu_integrations import FeishuIntegrationService


def _config(tmp_path) -> dict:
    return {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "feishu.db"),
        "_db_target": str(tmp_path / "feishu.db"),
        "_data_dir": str(tmp_path / "data"),
        "auth": {"required": True},
        "api": {"token": ""},
        "ai": {},
        "feishu": {"enabled": False},
        "wechat": {},
    }


def _account(account_id: str, owner_name: str) -> dict:
    return {
        "id": account_id,
        "name": f"{owner_name}公众号",
        "app_id": f"wx-{account_id}",
        "app_secret_encrypted": "encrypted",
        "model_id": "platform-text",
        "layout": {},
        "enabled": True,
    }


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-feishu-owner-isolation")
    config = _config(tmp_path)
    service = BatchService(config)
    root = service.db
    root.upsert_ai_model(
        {
            "id": "platform-text",
            "name": "平台文本模型",
            "provider_type": "openai_compatible",
            "api_base": "https://models.example.test/v1",
            "model": "text-model",
            "api_key_encrypted": "encrypted",
            "enabled": True,
        }
    )
    auth = AuthService(root)
    alice = auth.register("feishu-alice", "secret1")
    bob = auth.register("feishu-bob", "secret2")
    alice_db = root.for_user(str(alice["id"]))
    bob_db = root.for_user(str(bob["id"]))
    alice_db.upsert_official_account(_account("account-alice", "Alice"))
    bob_db.upsert_official_account(_account("account-bob", "Bob"))
    return config, service, alice, bob, alice_db, bob_db


def _save(db: Database, *, app_id: str, account_id: str) -> dict:
    return FeishuIntegrationService(db).save(
        app_id=app_id,
        app_secret=f"secret-{app_id}",
        verification_token=f"token-{app_id}",
        encrypt_key=f"encrypt-{app_id}",
        agent_model_id="platform-text",
        account_ids=[account_id],
        default_account_id=account_id,
        enabled=True,
    )


def test_each_user_has_an_independent_robot_and_app_id_is_unique(
    tmp_path, monkeypatch
) -> None:
    _, _, _, _, alice_db, bob_db = _setup(tmp_path, monkeypatch)

    alice = _save(alice_db, app_id="cli_alice", account_id="account-alice")
    bob = _save(bob_db, app_id="cli_bob", account_id="account-bob")

    assert alice["id"] != bob["id"]
    assert alice["callback_path"] != bob["callback_path"]
    assert alice["account_ids"] == ["account-alice"]
    assert bob["account_ids"] == ["account-bob"]
    assert public_feishu_settings(alice_db)["default_account_ids"] == [
        "account-alice"
    ]
    assert "secret-cli_alice" not in str(alice)
    assert "secret-cli_bob" not in str(bob)

    with pytest.raises(ValueError, match="已被其他系统用户"):
        _save(bob_db, app_id="cli_alice", account_id="account-bob")
    with pytest.raises(ValueError, match="不属于当前用户"):
        FeishuIntegrationService(alice_db).save(
            app_id="cli_alice",
            app_secret=None,
            verification_token=None,
            encrypt_key=None,
            agent_model_id="platform-text",
            account_ids=["account-bob"],
            default_account_id="account-bob",
        )


def test_pairing_sessions_and_event_ids_are_scoped_by_integration(
    tmp_path, monkeypatch
) -> None:
    _, _, _, _, alice_db, bob_db = _setup(tmp_path, monkeypatch)
    alice = _save(alice_db, app_id="cli_alice", account_id="account-alice")
    bob = _save(bob_db, app_id="cli_bob", account_id="account-bob")
    alice_service = FeishuIntegrationService(alice_db)
    bob_service = FeishuIntegrationService(bob_db)
    pairing = alice_service.create_pairing_code()

    assert not bob_service.consume_pairing_code(
        str(alice["id"]),
        text=pairing["message"],
        open_id="ou_bob",
        chat_id="oc_shared",
    )
    assert alice_service.consume_pairing_code(
        str(alice["id"]),
        text=pairing["message"],
        open_id="ou_alice",
        chat_id="oc_shared",
    )
    assert not alice_service.consume_pairing_code(
        str(alice["id"]),
        text=pairing["message"],
        open_id="ou_second",
        chat_id="oc_other",
    )

    alice_sessions = FeishuSessionStore(
        alice_db, integration_id=str(alice["id"])
    )
    bob_sessions = FeishuSessionStore(bob_db, integration_id=str(bob["id"]))
    alice_sessions.bind_batch("oc_shared", "batch-alice")
    bob_sessions.bind_batch("oc_shared", "batch-bob")
    assert alice_sessions.current_batch_id("oc_shared") == "batch-alice"
    assert bob_sessions.current_batch_id("oc_shared") == "batch-bob"
    assert alice_db.claim_feishu_event(str(alice["id"]), "event-same")
    assert bob_db.claim_feishu_event(str(bob["id"]), "event-same")
    assert not alice_db.claim_feishu_event(str(alice["id"]), "event-same")
    assert not alice_db.claim_feishu_event(str(bob["id"]), "event-forged")
    with pytest.raises(PermissionError, match="不属于当前用户"):
        alice_db.set_feishu_session(
            str(bob["id"]),
            "oc_shared",
            batch_id="batch-forged",
        )
    assert bob_sessions.current_batch_id("oc_shared") == "batch-bob"


def test_pairing_expires_locks_and_unbind_revokes_immediately(
    tmp_path, monkeypatch
) -> None:
    _, _, _, _, alice_db, _ = _setup(tmp_path, monkeypatch)
    integration = _save(
        alice_db, app_id="cli_alice", account_id="account-alice"
    )
    service = FeishuIntegrationService(alice_db)
    pairing = service.create_pairing_code()
    with alice_db.connect() as conn:
        conn.execute(
            """
            UPDATE feishu_integrations
            SET pairing_expires_at = ?
            WHERE id = ? AND owner_user_id = ?
            """,
            (
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                str(integration["id"]),
                alice_db.owner_user_id,
            ),
        )
    assert service.pairing_status()["status"] == "expired"
    assert not service.consume_pairing_code(
        str(integration["id"]),
        text=pairing["message"],
        open_id="ou_alice",
        chat_id="oc_alice",
    )

    pairing = service.create_pairing_code()
    for _ in range(5):
        assert not service.consume_pairing_code(
            str(integration["id"]),
            text="绑定 000000",
            open_id="ou_attacker",
            chat_id="oc_attacker",
        )
    assert service.pairing_status()["status"] == "locked"
    assert not service.consume_pairing_code(
        str(integration["id"]),
        text=pairing["message"],
        open_id="ou_alice",
        chat_id="oc_alice",
    )

    pairing = service.create_pairing_code()
    assert service.consume_pairing_code(
        str(integration["id"]),
        text=pairing["message"],
        open_id="ou_alice",
        chat_id="oc_alice",
    )
    service.unbind()
    effective = service.effective_for_owner()
    assert not effective.get("bound_open_id")
    assert service.public()["status"] == "waiting_pairing"
    assert service.set_enabled(False)["status"] == "disabled"
    assert service.set_enabled(True)["status"] == "waiting_pairing"


def test_group_message_is_rejected_before_event_execution(
    tmp_path, monkeypatch
) -> None:
    _, _, _, _, alice_db, _ = _setup(tmp_path, monkeypatch)
    integration = _save(
        alice_db, app_id="cli_alice", account_id="account-alice"
    )
    service = FeishuIntegrationService(alice_db)
    pairing = service.create_pairing_code()
    assert service.consume_pairing_code(
        str(integration["id"]),
        text=pairing["message"],
        open_id="ou_alice",
        chat_id="oc_private",
    )
    replies: list[str] = []
    dispatched: list[str] = []
    bot = FeishuBot.__new__(FeishuBot)
    bot.service = SimpleNamespace(db=alice_db)
    bot.integration_id = str(integration["id"])
    bot.app_id = "cli_alice"
    bot.bound_open_id = "ou_alice"
    bot._reply_text = lambda _message_id, text: replies.append(text)
    bot._dispatch_text = lambda *_args: dispatched.append("executed")
    bot._handle_message(
        SimpleNamespace(
            header=SimpleNamespace(event_id="event-group", app_id="cli_alice"),
            event=SimpleNamespace(
                sender=SimpleNamespace(
                    sender_id=SimpleNamespace(open_id="ou_alice")
                ),
                message=SimpleNamespace(
                    message_id="message-group",
                    chat_id="oc_group",
                    chat_type="group",
                    message_type="text",
                    content=json.dumps({"text": "改写文章"}),
                ),
            ),
        )
    )
    assert not dispatched
    assert replies and "仅支持绑定用户私聊" in replies[0]


def test_multiple_accounts_without_an_explicit_target_are_never_all_selected() -> None:
    executor = FeishuToolExecutor(
        service=SimpleNamespace(
            list_accounts=lambda: [
                {"id": "account-a", "name": "公众号A"},
                {"id": "account-b", "name": "公众号B"},
            ]
        ),
        config={},
        sessions=SimpleNamespace(),
        default_account_ids=["account-a", "account-b"],
        allowed_account_ids=["account-a", "account-b"],
        reply_text=lambda *_args: None,
        send_text=lambda *_args: None,
    )
    with pytest.raises(ValueError, match="请明确指定"):
        executor.resolve_accounts({})
    assert executor.resolve_accounts({"account_name": "公众号B"}) == [
        "account-b"
    ]


def test_cross_user_batch_and_job_ids_are_not_visible_to_the_other_robot(
    tmp_path, monkeypatch
) -> None:
    _, _, _, _, alice_db, bob_db = _setup(tmp_path, monkeypatch)
    job_id = alice_db.create_job(topic="Alice 私有文章")
    alice_db.create_batch(
        "batch-alice",
        topic="Alice 私有批次",
        source_integration_id="integration-alice",
    )
    alice_db.attach_batch_job(
        "batch-alice", job_id, "account-alice", "Alice公众号"
    )

    assert alice_db.get_batch("batch-alice") is not None
    assert alice_db.get_job(job_id) is not None
    assert bob_db.get_batch("batch-alice") is None
    assert bob_db.get_job(job_id) is None


def test_message_parser_keeps_app_and_chat_type() -> None:
    data = SimpleNamespace(
        header=SimpleNamespace(event_id="event-1", app_id="cli_a"),
        event=SimpleNamespace(
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_a")),
            message=SimpleNamespace(
                message_id="message-1",
                chat_id="oc_a",
                chat_type="p2p",
                message_type="text",
                content=json.dumps({"text": "查看进度"}, ensure_ascii=False),
            ),
        ),
    )
    message = parse_message_event(data)
    assert message.app_id == "cli_a"
    assert message.chat_type == "p2p"


def test_dedicated_webhook_challenge_signature_and_app_id(
    tmp_path, monkeypatch
) -> None:
    config, service, _, _, alice_db, _ = _setup(tmp_path, monkeypatch)
    integration = _save(
        alice_db, app_id="cli_alice", account_id="account-alice"
    )
    callback_key = str(integration["callback_path"]).rsplit("/", 1)[-1]
    dispatched: list[object] = []

    class _FakeBot:
        def __init__(self, _config, _service) -> None:
            pass

        def _on_message_event(self, data) -> None:
            dispatched.append(data)

    monkeypatch.setattr("app.feishu.webhook.FeishuBot", _FakeBot)
    app = create_api_app(config, service, start_feishu=False)
    with TestClient(app) as client:
        challenge = client.post(
            f"/api/feishu/events/{callback_key}",
            json={
                "challenge": "challenge-value",
                "token": "token-cli_alice",
                "type": "url_verification",
            },
        )
        assert challenge.status_code == 200
        assert challenge.json() == {"challenge": "challenge-value"}

        payload = {
            "schema": "2.0",
            "header": {
                "event_id": "event-1",
                "event_type": "im.message.receive_v1",
                "token": "token-cli_alice",
                "app_id": "cli_alice",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_alice"}},
                "message": {
                    "message_id": "message-1",
                    "chat_id": "oc_alice",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": json.dumps({"text": "帮助"}),
                },
            },
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        timestamp, nonce = "1770000000", "nonce-1"
        signature = hashlib.sha256(
            (timestamp + nonce + "encrypt-cli_alice").encode() + body
        ).hexdigest()
        accepted = client.post(
            f"/api/feishu/events/{callback_key}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Lark-Request-Timestamp": timestamp,
                "X-Lark-Request-Nonce": nonce,
                "X-Lark-Signature": signature,
            },
        )
        assert accepted.status_code == 200
        assert len(dispatched) == 1

        rejected = client.post(
            f"/api/feishu/events/{callback_key}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Lark-Request-Timestamp": timestamp,
                "X-Lark-Request-Nonce": nonce,
                "X-Lark-Signature": "wrong",
            },
        )
        assert rejected.status_code == 500
        assert rejected.json() == {"msg": "invalid event"}


def test_user_level_api_returns_only_the_authenticated_users_robot(
    tmp_path, monkeypatch
) -> None:
    config, service, alice, bob, alice_db, bob_db = _setup(tmp_path, monkeypatch)
    _save(alice_db, app_id="cli_alice", account_id="account-alice")
    _save(bob_db, app_id="cli_bob", account_id="account-bob")
    auth = AuthService(service.db)
    alice_token = auth.login("feishu-alice", "secret1")["token"]
    bob_token = auth.login("feishu-bob", "secret2")["token"]
    app = create_api_app(config, service, start_feishu=False)

    with TestClient(app) as client:
        alice_result = client.get(
            "/api/v1/me/feishu-integration",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        bob_result = client.get(
            "/api/v1/me/feishu-integration",
            headers={"Authorization": f"Bearer {bob_token}"},
        )

    assert alice_result.json()["app_id"] == "cli_alice"
    assert bob_result.json()["app_id"] == "cli_bob"
    assert alice_result.json()["account_ids"] == ["account-alice"]
    assert bob_result.json()["account_ids"] == ["account-bob"]
    assert alice_result.json()["callback_url"] != bob_result.json()["callback_url"]
    assert str(alice["id"]) != str(bob["id"])
