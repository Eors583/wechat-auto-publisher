from __future__ import annotations

import time

from app.ai.openai_compat import OpenAICompatClient
from app.db import Database


class LocalBrowserCompatClient(OpenAICompatClient):
    """Run an OpenAI-compatible localhost model through the user's browser."""

    def __init__(
        self,
        *,
        db: Database,
        model_id: str,
        model: str,
        provider_name: str,
        timeout: float = 620.0,
    ) -> None:
        super().__init__(
            api_key="browser-bridge",
            api_base="http://localhost",
            model=model,
            provider_name=provider_name,
            timeout=timeout,
        )
        self.db = db
        self.model_id = model_id

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.85,
        max_attempts: int = 1,
    ) -> str:
        del max_attempts
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        request_id = self.db.create_local_model_request(
            self.model_id,
            {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        deadline = time.monotonic() + self.timeout
        try:
            while time.monotonic() < deadline:
                request = self.db.get_local_model_request(request_id)
                if request is None:
                    raise RuntimeError("本地模型请求已丢失，请重新连接后再试")
                status = str(request.get("status") or "")
                if status == "completed":
                    content = str(request.get("response_text") or "").strip()
                    if not content:
                        raise RuntimeError("本地模型返回了空内容")
                    return content
                if status == "failed":
                    raise RuntimeError(
                        str(request.get("error") or "本地模型调用失败")
                    )
                time.sleep(0.25)
            self.db.fail_local_model_request(
                request_id,
                "等待本地模型超时；请保持网页打开并确认本地模型服务正在运行",
            )
            raise RuntimeError(
                "等待本地模型超时；请保持网页打开并确认本地模型服务正在运行"
            )
        finally:
            self.db.delete_local_model_request(request_id)


def local_chat_completions_url(api_base: str) -> str:
    base = str(api_base or "").strip().rstrip("/")
    return (
        f"{base}/chat/completions"
        if base.endswith(("/v1", "/v4"))
        else f"{base}/v1/chat/completions"
    )


__all__ = ["LocalBrowserCompatClient", "local_chat_completions_url"]
