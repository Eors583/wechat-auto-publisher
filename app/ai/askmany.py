from __future__ import annotations

import logging
from typing import Any

import httpx

from . import TitleResult, parse_title_output
from .usage import NormalizedUsage, emit_usage, normalize_chat_usage

logger = logging.getLogger(__name__)


class AskManyClient:
    """HTTP adapter for AskMany-compatible chat completions API."""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.askmany.ai",
        model: str = "default",
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = timeout

    def optimize_titles(self, prompt: str) -> TitleResult:
        if not self.api_key:
            raise RuntimeError("ASKMANY_API_KEY is empty")
        content = self._chat(prompt)
        result = parse_title_output(content)
        result.provider = "askmany"
        if len(result.titles) < 1:
            raise RuntimeError("AskMany returned no titles")
        return result

    def _chat(self, prompt: str) -> str:
        url = f"{self.api_base}/v1/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是公众号爆款标题专家，只输出结构化结果。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.9,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout) as client:
            try:
                resp = client.post(url, headers=headers, json=payload)
            except httpx.HTTPError:
                emit_usage(
                    provider="askmany",
                    provider_model=self.model,
                    usage=NormalizedUsage(),
                    status="failed",
                    error_code="transport_error",
                    client=self,
                )
                raise
            if resp.status_code >= 400:
                emit_usage(
                    provider="askmany",
                    provider_model=self.model,
                    usage=NormalizedUsage(),
                    status="failed",
                    request_id=str(resp.headers.get("x-request-id") or ""),
                    error_code=f"http_{resp.status_code}",
                    client=self,
                )
                raise RuntimeError(f"AskMany error: {resp.status_code} {resp.text[:300]}")
            data = resp.json()
        usage = normalize_chat_usage(data)
        response_id = str(data.get("id") or "")
        request_id = str(resp.headers.get("x-request-id") or response_id)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            emit_usage(
                provider="askmany",
                provider_model=self.model,
                usage=usage,
                status="failed",
                request_id=request_id,
                response_id=response_id,
                error_code="response_invalid",
                client=self,
            )
            raise RuntimeError(f"Unexpected AskMany response: {data}") from exc
        emit_usage(
            provider="askmany",
            provider_model=self.model,
            usage=usage,
            request_id=request_id,
            response_id=response_id,
            client=self,
        )
        return str(content)
