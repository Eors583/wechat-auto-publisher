from __future__ import annotations

from app.feishu.bot import _redact_sensitive_fields


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
