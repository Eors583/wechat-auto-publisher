from __future__ import annotations

import time

from app.ai.openai_compat import OpenAICompatClient
from app.ai.usage import emit_usage, estimated_text_usage
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
        timeout: float = 640.0,
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
            timeout_seconds=self.timeout,
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
                        emit_usage(
                            provider="local",
                            provider_model=self.model,
                            usage=estimated_text_usage(prompt, ""),
                            status="failed",
                            response_id=request_id,
                            error_code="response_empty",
                            client=self,
                        )
                        raise RuntimeError("本地模型返回了空内容")
                    emit_usage(
                        provider="local",
                        provider_model=self.model,
                        usage=estimated_text_usage(prompt, content),
                        response_id=request_id,
                        client=self,
                    )
                    return content
                if status == "failed":
                    emit_usage(
                        provider="local",
                        provider_model=self.model,
                        usage=estimated_text_usage(prompt, ""),
                        status="failed",
                        response_id=request_id,
                        error_code=str(request.get("error_code") or "provider_error"),
                        client=self,
                    )
                    raise RuntimeError(
                        str(request.get("error") or "本地模型调用失败")
                    )
                time.sleep(0.25)
            latest = self.db.get_local_model_request(request_id) or {}
            message = (
                "等待本机 Companion 超时；请检查设备在线、配对状态、Cockpit 和本机密钥"
                if str(latest.get("agent_id") or "").strip()
                else "等待本地模型超时；请保持网页打开并确认浏览器权限和本地服务状态"
            )
            self.db.fail_local_model_request(
                request_id,
                message,
                error_code=(
                    "agent.timeout"
                    if str(latest.get("agent_id") or "").strip()
                    else "browser.timeout"
                ),
            )
            emit_usage(
                provider="local",
                provider_model=self.model,
                usage=estimated_text_usage(prompt, ""),
                status="failed",
                response_id=request_id,
                error_code="timeout",
                client=self,
            )
            raise RuntimeError(message)
        finally:
            request = self.db.get_local_model_request(request_id)
            # Browser fallback rows can be removed immediately. Companion rows
            # remain for 24 hours so a lost HTTP acknowledgement can be retried
            # idempotently with the same attempt_id and nonce.
            if not request or not str(request.get("agent_id") or "").strip():
                self.db.delete_local_model_request(request_id)


def local_chat_completions_url(api_base: str) -> str:
    base = str(api_base or "").strip().rstrip("/")
    return (
        f"{base}/chat/completions"
        if base.endswith(("/v1", "/v4"))
        else f"{base}/v1/chat/completions"
    )


__all__ = ["LocalBrowserCompatClient", "local_chat_completions_url"]
