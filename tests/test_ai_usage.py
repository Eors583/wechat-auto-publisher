from __future__ import annotations

import pytest

from app.ai.usage import (
    NormalizedUsage,
    TOKEN_USAGE_UNAVAILABLE,
    bind_usage_recorder,
    emit_usage,
    estimated_text_usage,
    normalize_chat_usage,
    normalize_gemini_usage,
    normalize_responses_usage,
    usage_attempt,
)
from app.ai.failover import FailoverRewriter


def test_openai_chat_and_responses_usage_are_normalized_without_double_counting() -> None:
    chat = normalize_chat_usage(
        {
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 50,
                "total_tokens": 170,
                "prompt_tokens_details": {"cached_tokens": 80},
                "completion_tokens_details": {"reasoning_tokens": 30},
            }
        }
    )
    responses = normalize_responses_usage(
        {
            "usage": {
                "input_tokens": 40,
                "output_tokens": 25,
                "total_tokens": 65,
                "input_tokens_details": {"cached_tokens": 10},
                "output_tokens_details": {"reasoning_tokens": 20},
            }
        }
    )

    assert chat == NormalizedUsage(120, 80, 50, 30, 170, source="provider_actual")
    assert responses.output_tokens == 25
    assert responses.reasoning_tokens == 20
    assert responses.total_tokens == 65
    assert chat.raw_usage["total_tokens"] == 170
    assert "choices" not in chat.raw_usage


def test_gemini_usage_metadata_is_provider_actual() -> None:
    usage = normalize_gemini_usage(
        {
            "usageMetadata": {
                "promptTokenCount": 31,
                "cachedContentTokenCount": 7,
                "candidatesTokenCount": 12,
                "thoughtsTokenCount": 4,
                "totalTokenCount": 43,
            }
        }
    )

    assert usage.input_tokens == 31
    assert usage.cached_input_tokens == 7
    assert usage.output_tokens == 12
    assert usage.reasoning_tokens == 4
    assert usage.source == "provider_actual"


def test_missing_provider_usage_is_unavailable_instead_of_actual_zero() -> None:
    chat = normalize_chat_usage(
        {
            "id": "response-without-usage",
            "choices": [{"message": {"content": "OK"}}],
        }
    )
    gemini = normalize_gemini_usage({"text": "OK"})

    assert chat.token_status == TOKEN_USAGE_UNAVAILABLE
    assert gemini.token_status == TOKEN_USAGE_UNAVAILABLE
    assert chat.source == gemini.source == "unknown"
    assert chat.total_tokens == gemini.total_tokens == 0
    assert chat.raw_usage == gemini.raw_usage == {}


def test_estimated_usage_stays_separate_from_the_strict_status_enum() -> None:
    usage = estimated_text_usage("四个字符", "返回值")

    assert usage.source == "estimated"
    assert usage.token_status == TOKEN_USAGE_UNAVAILABLE
    assert usage.total_tokens > 0

    with pytest.raises(ValueError, match="服务商返回"):
        NormalizedUsage(
            input_tokens=1,
            source="estimated",
            token_status="RECORDED",
        )


def test_recorder_failure_is_fail_open() -> None:
    def broken_recorder(_record) -> None:
        raise RuntimeError("meter unavailable")

    with bind_usage_recorder(broken_recorder):
        emit_usage(
            provider="openai",
            provider_model="gpt-test",
            usage=NormalizedUsage(input_tokens=1),
        )


def test_failed_fallback_attempt_is_retained_but_not_contributing() -> None:
    records = []
    with bind_usage_recorder(records.append):
        try:
            with usage_attempt():
                emit_usage(
                    provider="primary",
                    provider_model="model-a",
                    usage=NormalizedUsage(input_tokens=5),
                )
                raise ValueError("quality gate rejected output")
        except ValueError:
            pass
        with usage_attempt():
            emit_usage(
                provider="fallback",
                provider_model="model-b",
                usage=NormalizedUsage(input_tokens=7),
            )

    assert [record.contributes_to_result for record in records] == [False, True]
    assert [record.provider for record in records] == ["primary", "fallback"]


def test_failover_tags_custom_byok_as_customer_funded() -> None:
    rewriter = FailoverRewriter(
        {
            "ai": {
                "primary": "custom-user-model",
                "custom_models": {
                    "custom-user-model": {
                        "provider_type": "openai_compatible",
                        "api_key": "placeholder",
                        "api_base": "https://example.invalid/v1",
                        "model": "user-model",
                    }
                },
            }
        }
    )

    assert (
        rewriter._clients["custom-user-model"]._usage_funding_source
        == "customer"
    )
