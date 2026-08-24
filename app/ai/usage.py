from __future__ import annotations

import logging
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterator, Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NormalizedUsage:
    """Provider-neutral counters; never contains prompts, content, or secrets."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    image_count: int = 0
    fixed_units: int = 0
    source: str = "unknown"

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "image_count",
            "fixed_units",
        ):
            object.__setattr__(self, name, max(0, int(getattr(self, name) or 0)))
        object.__setattr__(
            self,
            "cached_input_tokens",
            min(self.cached_input_tokens, self.input_tokens),
        )
        if not self.total_tokens:
            object.__setattr__(
                self,
                "total_tokens",
                self.input_tokens + self.output_tokens,
            )


@dataclass(frozen=True, slots=True)
class UsageRecord:
    provider: str
    provider_model: str
    modality: str
    usage: NormalizedUsage
    status: str = "succeeded"
    request_id: str = ""
    response_id: str = ""
    error_code: str = ""
    model_id: str = ""
    funding_source: str = "platform"
    contributes_to_result: bool = True


UsageRecorder = Callable[[UsageRecord], None]
_ACTIVE_RECORDER: ContextVar[UsageRecorder | None] = ContextVar(
    "wechat_publisher_usage_recorder",
    default=None,
)
_USAGE_ATTRIBUTES: ContextVar[dict[str, str]] = ContextVar(
    "wechat_publisher_usage_attributes",
    default={},
)
_ATTEMPT_BUFFER: ContextVar[list[UsageRecord] | None] = ContextVar(
    "wechat_publisher_usage_attempt_buffer",
    default=None,
)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    for method_name in ("model_dump", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                result = method()
            except Exception:  # noqa: BLE001
                continue
            if isinstance(result, Mapping):
                return result
    if value is None:
        return {}
    names = (
        "usage",
        "usage_metadata",
        "usageMetadata",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "cached_content_token_count",
        "thoughts_token_count",
        "input_tokens",
        "output_tokens",
        "input_tokens_details",
        "output_tokens_details",
        "prompt_tokens_details",
        "completion_tokens_details",
        "id",
    )
    return {
        name: getattr(value, name)
        for name in names
        if getattr(value, name, None) is not None
    }


def _int(data: Mapping[str, Any], *names: str) -> int:
    for name in names:
        value = data.get(name)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def normalize_chat_usage(response: Any) -> NormalizedUsage:
    data = _mapping(response)
    usage = _mapping(data.get("usage") or data)
    prompt_details = _mapping(
        usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    )
    completion_details = _mapping(
        usage.get("completion_tokens_details") or usage.get("output_tokens_details")
    )
    input_tokens = _int(usage, "prompt_tokens", "input_tokens")
    output_tokens = _int(usage, "completion_tokens", "output_tokens")
    source = "provider_actual" if usage else "unknown"
    return NormalizedUsage(
        input_tokens=input_tokens,
        cached_input_tokens=_int(prompt_details, "cached_tokens"),
        output_tokens=output_tokens,
        reasoning_tokens=_int(completion_details, "reasoning_tokens"),
        total_tokens=_int(usage, "total_tokens"),
        source=source,
    )


def normalize_responses_usage(response: Any) -> NormalizedUsage:
    return normalize_chat_usage(response)


def normalize_gemini_usage(response: Any) -> NormalizedUsage:
    data = _mapping(response)
    usage = _mapping(
        data.get("usage_metadata") or data.get("usageMetadata") or data
    )
    input_tokens = _int(usage, "prompt_token_count", "promptTokenCount")
    output_tokens = _int(
        usage,
        "candidates_token_count",
        "candidatesTokenCount",
    )
    return NormalizedUsage(
        input_tokens=input_tokens,
        cached_input_tokens=_int(
            usage,
            "cached_content_token_count",
            "cachedContentTokenCount",
        ),
        output_tokens=output_tokens,
        reasoning_tokens=_int(
            usage,
            "thoughts_token_count",
            "thoughtsTokenCount",
        ),
        total_tokens=_int(usage, "total_token_count", "totalTokenCount"),
        source="provider_actual" if usage else "unknown",
    )


def estimated_text_usage(prompt: str, output: str) -> NormalizedUsage:
    # Estimation is deliberately labelled and is never promoted to actual usage.
    return NormalizedUsage(
        input_tokens=math.ceil(len(str(prompt or "")) / 4),
        output_tokens=math.ceil(len(str(output or "")) / 4),
        source="estimated",
    )


def fixed_usage(*, image_count: int = 0, fixed_units: int = 1) -> NormalizedUsage:
    return NormalizedUsage(
        image_count=image_count,
        fixed_units=fixed_units,
        source="provider_fixed",
    )


def tag_client(client: Any, *, model_id: str, funding_source: str) -> Any:
    client._usage_model_id = str(model_id or "")
    client._usage_funding_source = str(funding_source or "platform")
    return client


def client_usage_attributes(client: Any) -> dict[str, str]:
    return {
        "model_id": str(getattr(client, "_usage_model_id", "") or ""),
        "funding_source": str(
            getattr(client, "_usage_funding_source", "platform") or "platform"
        ),
    }


@contextmanager
def bind_usage_recorder(recorder: UsageRecorder | None) -> Iterator[None]:
    token = _ACTIVE_RECORDER.set(recorder)
    try:
        yield
    finally:
        _ACTIVE_RECORDER.reset(token)


@contextmanager
def usage_attributes(**attributes: str) -> Iterator[None]:
    merged = {**_USAGE_ATTRIBUTES.get(), **{key: str(value) for key, value in attributes.items()}}
    token = _USAGE_ATTRIBUTES.set(merged)
    try:
        yield
    finally:
        _USAGE_ATTRIBUTES.reset(token)


@contextmanager
def usage_attempt() -> Iterator[None]:
    """Delay one fallback attempt so rejected output is marked non-contributing."""

    buffered: list[UsageRecord] = []
    parent = _ATTEMPT_BUFFER.get()
    token = _ATTEMPT_BUFFER.set(buffered)
    succeeded = False
    try:
        yield
        succeeded = True
    finally:
        _ATTEMPT_BUFFER.reset(token)
        records = (
            buffered
            if succeeded
            else [replace(item, contributes_to_result=False) for item in buffered]
        )
        if parent is not None:
            parent.extend(records)
        else:
            recorder = _ACTIVE_RECORDER.get()
            if recorder is not None:
                for item in records:
                    _safe_record(recorder, item)


def emit_usage(
    *,
    provider: str,
    provider_model: str,
    usage: NormalizedUsage,
    modality: str = "text",
    status: str = "succeeded",
    request_id: str = "",
    response_id: str = "",
    error_code: str = "",
    client: Any | None = None,
    model_id: str = "",
    funding_source: str = "",
    contributes_to_result: bool = True,
) -> None:
    attributes = dict(_USAGE_ATTRIBUTES.get())
    if client is not None:
        attributes.update(client_usage_attributes(client))
    record = UsageRecord(
        provider=str(provider or "unknown")[:80],
        provider_model=str(provider_model or "")[:160],
        modality=str(modality or "text")[:20],
        usage=usage,
        status=str(status or "unknown")[:24],
        request_id=str(request_id or "")[:200],
        response_id=str(response_id or "")[:200],
        error_code=str(error_code or "")[:120],
        model_id=str(model_id or attributes.get("model_id") or "")[:160],
        funding_source=str(
            funding_source or attributes.get("funding_source") or "platform"
        )[:24],
        contributes_to_result=bool(contributes_to_result),
    )
    buffer = _ATTEMPT_BUFFER.get()
    if buffer is not None:
        buffer.append(record)
        return
    recorder = _ACTIVE_RECORDER.get()
    if recorder is not None:
        _safe_record(recorder, record)


def _safe_record(recorder: UsageRecorder, record: UsageRecord) -> None:
    try:
        recorder(record)
    except Exception:  # noqa: BLE001
        logger.exception("shadow usage recording failed; business call continues")


__all__ = [
    "NormalizedUsage",
    "UsageRecord",
    "bind_usage_recorder",
    "emit_usage",
    "estimated_text_usage",
    "fixed_usage",
    "normalize_chat_usage",
    "normalize_gemini_usage",
    "normalize_responses_usage",
    "tag_client",
    "usage_attempt",
    "usage_attributes",
]
