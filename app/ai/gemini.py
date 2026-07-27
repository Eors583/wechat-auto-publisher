from __future__ import annotations

import logging

from . import RewriteResult, parse_rewrite_output

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
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        content = getattr(response, "text", None) or ""
        if not str(content).strip():
            raise RuntimeError("Gemini returned empty content")
        return str(content)
