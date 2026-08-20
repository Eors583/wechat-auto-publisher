from __future__ import annotations

import inspect
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.ai.model_registry import encrypt_api_key
from app.api.server import create_api_app
from app.config import load_config
from app.services.auth import AuthService
from app.services.batches import BatchService
from app.services.wechat_commands import WeChatCommandService
from app.wechat.client import _utf8_chunks
from app.wechat.messages import (
    WeChatMessageCipher,
    message_signature,
    parse_message_xml,
    render_text_reply,
)


def _service(tmp_path) -> tuple[dict, BatchService, str, WeChatCommandService]:
    config = {
        **load_config(),
        "_db_path": str(tmp_path / "wechat-commands.db"),
        "api": {"token": "test-api-token"},
        "feishu": {"enabled": False},
    }
    batch_service = BatchService(config)
    owner = str(AuthService(batch_service.db).ensure_default_admin()["id"])
    scoped = batch_service.db.for_user(owner)
    scoped.upsert_official_account(
        {
            "id": "account-1",
            "name": "测试公众号",
            "app_id": "wx-test-app",
            "app_secret_encrypted": encrypt_api_key("wechat-secret"),
            "model_id": "",
            "layout": {},
            "enabled": True,
        }
    )
    return config, batch_service, owner, WeChatCommandService(scoped, config)


def _message_xml(content: str, *, message_id: str) -> str:
    return (
        "<xml>"
        "<ToUserName>wx-test-app</ToUserName>"
        "<FromUserName>openid-admin</FromUserName>"
        "<CreateTime>1720000000</CreateTime>"
        "<MsgType>text</MsgType>"
        f"<Content>{content}</Content>"
        f"<MsgId>{message_id}</MsgId>"
        "</xml>"
    )


def test_safe_mode_cipher_round_trip_preserves_chinese_message() -> None:
    key = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ"
    cipher = WeChatMessageCipher(key, "wx-test-app")
    source = _message_xml("改写这篇公开文章", message_id="1001")

    encrypted = cipher.encrypt(source)
    assert cipher.decrypt(encrypted) == source

    message = parse_message_xml(source)
    reply = render_text_reply(message, "已收到，正在处理")
    wrapper = cipher.render_encrypted_reply(
        reply,
        token="callback-token",
        timestamp="1720000001",
        nonce="nonce-1",
    )
    root = ET.fromstring(wrapper)
    assert cipher.decrypt(str(root.findtext("Encrypt"))) == reply
    assert root.findtext("MsgSignature") == message_signature(
        "callback-token",
        "1720000001",
        "nonce-1",
        str(root.findtext("Encrypt")),
    )


def test_customer_service_text_chunks_never_split_utf8_characters() -> None:
    chunks = _utf8_chunks("改写" * 800, max_bytes=1800)

    assert "".join(chunks) == "改写" * 800
    assert len(chunks) > 1
    assert all(len(chunk.encode("utf-8")) <= 1800 for chunk in chunks)


def test_wechat_reuses_feishu_agent_tools_and_progress_notifications() -> None:
    source = inspect.getsource(WeChatCommandService.run_command)

    assert "FeishuToolAgent" in source
    assert "FeishuToolExecutor" in source
    assert "FeishuSessionStore" in source
    assert "FeishuProgressReporter" in source
    assert "service.add_listener(notify_batch_changed)" in source
    assert 'source_channel="wechat"' in source


def test_command_settings_encrypt_secrets_and_pair_one_open_id(tmp_path) -> None:
    _config, _batch, _owner, service = _service(tmp_path)

    generated = service.provision("account-1")
    raw = service.db.get_user_setting("wechat.command.account-1") or ""
    assert generated["token"] not in raw
    assert generated["encoding_aes_key"] not in raw
    assert service.effective_settings("account-1")["token"] == generated["token"]

    command = service.create_pairing_code("account-1")
    authorized, paired = service.authorize_or_pair(
        "account-1",
        open_id="openid-admin",
        text=command,
    )
    assert (authorized, paired) == (True, True)
    assert service.authorize_or_pair(
        "account-1",
        open_id="openid-admin",
        text="帮助",
    ) == (True, False)
    assert service.authorize_or_pair(
        "account-1",
        open_id="openid-other",
        text=command,
    ) == (False, False)


def test_plain_callback_verification_and_authorized_command(
    tmp_path,
    monkeypatch,
) -> None:
    config, batch_service, owner, service = _service(tmp_path)
    generated = service.provision("account-1")
    pairing_command = service.create_pairing_code("account-1")
    app = create_api_app(config, batch_service, start_feishu=False)
    path = f"/api/v1/wechat/commands/{owner}/account-1"
    timestamp = "1720000000"
    nonce = "nonce-test"
    query = {
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": message_signature(generated["token"], timestamp, nonce),
    }
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        WeChatCommandService,
        "run_command",
        lambda self, account_id, open_id, text: calls.append(
            (account_id, open_id, text)
        ),
    )

    with TestClient(app) as client:
        verified = client.get(path, params={**query, "echostr": "verified"})
        assert verified.status_code == 200
        assert verified.text == "verified"

        paired = client.post(
            path,
            params=query,
            content=_message_xml(pairing_command, message_id="message-1"),
            headers={"Content-Type": "application/xml"},
        )
        assert paired.status_code == 200
        assert "绑定成功" in paired.text

        accepted = client.post(
            path,
            params=query,
            content=_message_xml(
                "https://mp.weixin.qq.com/s/example", message_id="message-2"
            ),
            headers={"Content-Type": "application/xml"},
        )
        assert accepted.status_code == 200
        assert "后台处理" in accepted.text

    assert calls == [
        ("account-1", "openid-admin", "https://mp.weixin.qq.com/s/example")
    ]


def test_callback_rejects_invalid_signature(tmp_path) -> None:
    config, batch_service, owner, service = _service(tmp_path)
    service.provision("account-1")
    app = create_api_app(config, batch_service, start_feishu=False)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/wechat/commands/{owner}/account-1",
            params={
                "timestamp": "1720000000",
                "nonce": "nonce-test",
                "signature": "invalid",
                "echostr": "must-not-return",
            },
        )

    assert response.status_code == 403
    assert "must-not-return" not in response.text


def test_safe_mode_callback_verification_decrypts_echo(tmp_path) -> None:
    config, batch_service, owner, service = _service(tmp_path)
    generated = service.provision("account-1")
    cipher = WeChatMessageCipher(generated["encoding_aes_key"], "wx-test-app")
    timestamp = "1720000002"
    nonce = "safe-nonce"
    encrypted_echo = cipher.encrypt("safe-mode-verified")
    app = create_api_app(config, batch_service, start_feishu=False)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/wechat/commands/{owner}/account-1",
            params={
                "timestamp": timestamp,
                "nonce": nonce,
                "encrypt_type": "aes",
                "msg_signature": message_signature(
                    generated["token"],
                    timestamp,
                    nonce,
                    encrypted_echo,
                ),
                "echostr": encrypted_echo,
            },
        )

    assert response.status_code == 200
    assert response.text == "safe-mode-verified"
