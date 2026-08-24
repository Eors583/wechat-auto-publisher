from __future__ import annotations

import logging

from . import RewriteResult, parse_rewrite_output
from .usage import NormalizedUsage, emit_usage, normalize_gemini_usage

logger = logging.getLogger(__name__)


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def rewrite(self, prompt: str) -> RewriteResult:
        content = self.complete(prompt)
        result = parse_rewrite_output(content)
        result.provider = "gemini"
        return result

    def complete(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is empty")
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("google-genai is not installed") from exc

        client = genai.Client(api_key=self.api_key)
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
        except Exception:
            emit_usage(
                provider="gemini",
                provider_model=self.model,
                usage=NormalizedUsage(),
                status="failed",
                error_code="provider_error",
                client=self,
            )
            raise
        content = getattr(response, "text", None) or ""
        if not str(content).strip():
            emit_usage(
                provider="gemini",
                provider_model=self.model,
                usage=normalize_gemini_usage(response),
                status="failed",
                response_id=str(getattr(response, "response_id", "") or ""),
                error_code="response_empty",
                client=self,
            )
            raise RuntimeError("Gemini returned empty content")
        emit_usage(
            provider="gemini",
            provider_model=self.model,
            usage=normalize_gemini_usage(response),
            response_id=str(getattr(response, "response_id", "") or ""),
            client=self,
        )
        return str(content)
