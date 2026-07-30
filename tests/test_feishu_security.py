from __future__ import annotations

from types import SimpleNamespace

from app.feishu.bot import FeishuBot, _redact_sensitive_fields


def test_sensitive_configuration_values_are_removed_before_agent_planning() -> None:
    text = (
        "确认保存模型密钥配置\n"
        "API Key：sk-secret-value\n"
        "App Secret: app-secret-value\n"
        "token=123456789\n"
        "Cookie: wxuin=1; pass_ticket=abc; data_bizuin=2"
    )

    redacted, values = _redact_sensitive_fields(text)

    assert values == {
        "api_key": "sk-secret-value",
        "app_secret": "app-secret-value",
        "token": "123456789",
        "cookie": "wxuin=1; pass_ticket=abc; data_bizuin=2",
    }
    for secret in values.values():
        assert secret not in redacted
    assert redacted.count("已安全提取") == 4


def test_sensitive_configuration_message_is_rejected_before_agent_planning() -> None:
    replies: list[str] = []
    planner_calls: list[str] = []
    legacy_calls: list[str] = []
    bot = FeishuBot.__new__(FeishuBot)
    bot.sessions = SimpleNamespace(
        pending_action=lambda _chat_id: None,
        confirm_pending_action=lambda _chat_id, _text: None,
    )
    bot._reply_text = lambda _message_id, text: replies.append(text)
    bot.agent = SimpleNamespace(
        plan=lambda text, **_kwargs: planner_calls.append(text),
    )
    bot.legacy = SimpleNamespace(
        dispatch=lambda text, *_args: legacy_calls.append(text),
    )

    bot._dispatch_text(
        "请保存 API Key：sk-do-not-send-to-agent",
        "message-1",
        "chat-1",
        "user-1",
    )

    assert not planner_calls
    assert not legacy_calls
    assert len(replies) == 1
    assert "没有保存" in replies[0]
    assert "本机桌面端" in replies[0]
    assert "sk-do-not-send-to-agent" not in replies[0]
