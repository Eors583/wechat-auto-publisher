from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

import httpx

from . import (
    ARTICLE_DIGEST_PROMPT,
    EMPHASIS_PROMPT,
    SUBTITLE_CANDIDATE_COUNT,
    TITLE_CANDIDATE_COUNT,
    RewriteResult,
    TitleResult,
    parse_rewrite_output,
    parse_title_output,
)
from .usage import emit_usage, fixed_usage

logger = logging.getLogger(__name__)

_request_gate_lock = threading.Lock()
_next_request_at: dict[tuple[str, str, str], float] = {}

_INLINE_TEXT_LIMIT = 4000


def _message_content(
    prompt: str,
    *,
    file_id: str = "",
) -> str | list[dict[str, str]]:
    """Reference long prompts by uploaded file so Base64 is never token-counted."""

    text = str(prompt or "")
    if len(text) <= _INLINE_TEXT_LIMIT:
        return text
    if not str(file_id or "").strip():
        raise ValueError("Long Manus prompts require an uploaded file_id")
    return [
        {
            "type": "text",
            "text": (
                "完整任务说明和全部原始材料位于附件 task-input.txt。请先完整读取附件，"
                "严格按其中要求执行，并通过本任务的结构化输出返回结果。"
            ),
        },
        {
            "type": "file",
            "file_id": str(file_id).strip(),
        },
    ]


class ManusTransportError(RuntimeError):
    """A Manus network failure with explicit whole-task retry semantics."""

    def __init__(
        self,
        message: str,
        *,
        safe_to_restart_task: bool,
    ) -> None:
        self.safe_to_restart_task = bool(safe_to_restart_task)
        super().__init__(message)


def _credential_scope(api_base: str, api_key: str) -> str:
    """Build a non-sensitive process-local rate-limit scope."""

    value = f"{api_base.rstrip('/')}|{api_key}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _wait_for_request_slot(
    *,
    api_base: str,
    api_key: str,
    endpoint: str,
    min_interval: float,
) -> None:
    """Space calls sharing one Manus credential without serializing task work."""

    interval = max(0.0, float(min_interval))
    if interval <= 0:
        return
    key = (_credential_scope(api_base, api_key), endpoint, "v2")
    while True:
        with _request_gate_lock:
            now = time.monotonic()
            wait_seconds = max(0.0, _next_request_at.get(key, 0.0) - now)
            if wait_seconds <= 0:
                _next_request_at[key] = now + interval
                return
        time.sleep(wait_seconds)


class ManusAPIError(RuntimeError):
    """Structured Manus API failure with retry and support metadata."""

    NON_RETRYABLE_CODES = {
        "invalid_argument",
        "unauthenticated",
        "permission_denied",
    }

    def __init__(
        self,
        code: str | int,
        message: str,
        *,
        request_id: str = "",
        status_code: int | None = None,
    ) -> None:
        self.code = str(code or status_code or "unknown")
        self.request_id = str(request_id or "").strip()
        self.status_code = status_code
        detail = f"Manus API error {self.code}: {message}"
        if self.request_id:
            detail += f" (request_id: {self.request_id})"
        super().__init__(detail)

    @property
    def retryable(self) -> bool:
        if self.code.casefold() in self.NON_RETRYABLE_CODES:
            return False
        # Most 4xx responses describe a permanently invalid request. Retrying
        # the same payload only creates noise and cost; 408/429 are the two
        # client-status exceptions that can reasonably succeed later.
        if (
            self.status_code is not None
            and 400 <= self.status_code < 500
            and self.status_code not in {408, 429}
        ):
            return False
        return True


def is_non_retryable_manus_error(exc: BaseException | str) -> bool:
    """Recognize permanent Manus request errors, including legacy wrappers."""

    if isinstance(exc, ManusTransportError):
        return not exc.safe_to_restart_task
    if isinstance(exc, ManusAPIError):
        return not exc.retryable
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "manus api error invalid_argument",
            "manus api error unauthenticated",
            "manus api error permission_denied",
        )
    )


class ManusClient:
    """Manus API v2 adapter using asynchronous tasks and structured output."""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.manus.ai",
        model: str = "manus-1.6",
        timeout: float = 600.0,
        poll_interval: float = 2.0,
        create_min_interval: float = 6.25,
        poll_min_interval: float = 0.7,
        transport_retries: int = 4,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.poll_interval = poll_interval
        # Manus v2 applies rate limits per user.  Stagger task creation while
        # keeping already-created tasks running concurrently.
        self.create_min_interval = max(0.0, float(create_min_interval))
        self.poll_min_interval = max(0.0, float(poll_min_interval))
        self.transport_retries = max(1, int(transport_retries))

    def rewrite(self, prompt: str) -> RewriteResult:
        single_pass_prompt = (
            "请直接基于提供的材料，在一次任务中完成正文、标题、副标题和摘要，不要联网研究，"
            "不要调用浏览器、终端、代码、文件或其他工具，也不要把标题生成拆成后续任务。\n"
            "body 去除空白后不得少于 2000 字，不设置严格字数上限，也不需要精确统计或"
            f"反复截断字数；titles 必须给出恰好 {TITLE_CANDIDATE_COUNT} 个互不重复的"
            f"标题；subtitles 必须给出恰好 {SUBTITLE_CANDIDATE_COUNT} 个互不重复的"
            f"副标题，并与标题形成信息互补；digest 必须满足：{ARTICLE_DIGEST_PROMPT}"
            "该规则优先于运营提示词中的旧规则。\n"
            "最终结果将由程序按结构化 JSON 接收，请不要省略任何字段。\n\n"
            + prompt
        )
        value = self._run_structured_task(
            single_pass_prompt,
            _rewrite_schema(),
            title="公众号文章改写",
        )
        raw = json.dumps(value, ensure_ascii=False)
        result = parse_rewrite_output(raw)
        result.provider = "manus"
        return result

    def expand_rewrite(
        self,
        topic: str,
        draft_body: str,
        *,
        target_chars: int = 2500,
    ) -> RewriteResult:
        prompt = (
            "请直接扩写下面的微信公众号正文，并返回一篇完整可直接发布的新版本。"
            "不要联网研究，不要调用浏览器、终端、代码、文件或其他工具。\n"
            "硬性要求：正文去除空白后绝不能少于 2000 字，不设置严格字数上限，也不需要"
            "精确统计或反复截断字数；保留原有核心观点与自然小标题，为每个观点补充原因、"
            "案例、数据或对比，每个观点至少使用两个自然段说明；段落间用空行分隔。\n"
            f"{EMPHASIS_PROMPT}\n"
            f"同时返回 {TITLE_CANDIDATE_COUNT} 个标题和 "
            f"{SUBTITLE_CANDIDATE_COUNT} 个副标题，以及 digest；digest 必须满足："
            f"{ARTICLE_DIGEST_PROMPT}不要解释任务，不要省略正文。\n\n"
            f"【话题】{topic}\n\n【待扩写正文】\n{draft_body[:12000]}"
        )
        value = self._run_structured_task(
            prompt,
            _rewrite_schema(),
            title="公众号文章扩写",
        )
        result = parse_rewrite_output(json.dumps(value, ensure_ascii=False))
        result.provider = "manus"
        return result

    def complete(self, prompt: str) -> str:
        value = self._run_structured_task(
            prompt,
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            title="公众号内容生成",
        )
        return str(value.get("text") or "").strip()

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        title: str = "公众号结构化内容生成",
    ) -> dict[str, Any]:
        """Return a JSON object through Manus' native structured output.

        Wrapping an object-shaped response in a single ``text`` field lets the
        provider legally return an incomplete fragment such as ``"{"`` while
        still marking structured extraction successful.  Callers that own a
        concrete JSON contract should pass it directly instead.
        """

        return self._run_structured_task(prompt, schema, title=title)

    def optimize_titles(self, prompt: str) -> TitleResult:
        value = self._run_structured_task(
            prompt,
            {
                "type": "object",
                "properties": {
                    "titles": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "required": ["titles"],
                "additionalProperties": False,
            },
            title="公众号标题优化",
        )
        raw = json.dumps(value, ensure_ascii=False)
        result = parse_title_output(raw)
        result.provider = "manus"
        return result

    def _run_structured_task(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        title: str,
    ) -> dict[str, Any]:
        try:
            value, task_id, credit_usage = self._run_structured_task_once(
                prompt,
                schema,
                title=title,
            )
        except Exception as exc:
            emit_usage(
                provider="manus",
                provider_model=self.model,
                usage=fixed_usage(),
                status="failed",
                request_id=str(getattr(exc, "request_id", "") or ""),
                error_code=str(getattr(exc, "code", "") or type(exc).__name__)[:120],
                client=self,
            )
            raise
        emit_usage(
            provider="manus",
            provider_model=self.model,
            usage=fixed_usage(provider_credits=credit_usage),
            request_id=task_id,
            client=self,
        )
        return value

    def _run_structured_task_once(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        title: str,
    ) -> tuple[dict[str, Any], str, int | None]:
        if not self.api_key:
            raise RuntimeError("MANUS_API_KEY is empty")

        headers = {
            "x-manus-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            file_id = (
                self._upload_prompt_file(client, headers, prompt)
                if len(str(prompt or "")) > _INLINE_TEXT_LIMIT
                else ""
            )
            payload: dict[str, Any] = {
                "message": {
                    "content": _message_content(prompt, file_id=file_id)
                },
                "agent_profile": self.model,
                "interactive_mode": False,
                "hide_in_task_list": True,
                "share_visibility": "private",
                "title": title,
                "structured_output_schema": schema,
            }
            deadline = time.monotonic() + self.timeout
            created = self._request_json(
                client,
                "POST",
                "/v2/task.create",
                headers=headers,
                json_body=payload,
            )
            task_id = str(
                created.get("task_id")
                or (created.get("task") or {}).get("id")
                or ""
            ).strip()
            if not task_id:
                raise RuntimeError("Manus task.create did not return task_id")

            logger.info("Manus task created: %s", task_id)
            # A newly-created Manus task can briefly be unavailable to the
            # listMessages read path.  Keep polling the same task instead of
            # failing over and creating a duplicate task immediately.
            task_created_at = time.monotonic()
            task_visibility_grace = min(60.0, max(15.0, self.timeout / 10))
            not_found_attempts = 0
            last_status = "running"
            stopped_at: float | None = None
            while time.monotonic() < deadline:
                try:
                    events = self._request_json(
                        client,
                        "GET",
                        "/v2/task.listMessages",
                        headers=headers,
                        params={"task_id": task_id, "order": "desc", "limit": 100},
                    ).get("messages") or []
                except RuntimeError as exc:
                    task_age = time.monotonic() - task_created_at
                    if _is_task_not_found(exc) and task_age < task_visibility_grace:
                        not_found_attempts += 1
                        retry_delay = min(
                            max(self.poll_interval, 1.0) * not_found_attempts,
                            5.0,
                        )
                        logger.warning(
                            "Manus task %s is not visible yet; retrying the same "
                            "task in %.1fs (attempt %d)",
                            task_id,
                            retry_delay,
                            not_found_attempts,
                        )
                        time.sleep(retry_delay)
                        continue
                    raise

                for event in events:
                    if event.get("type") == "structured_output_result":
                        result = event.get("structured_output_result") or {}
                        if result.get("success") is False:
                            raise RuntimeError(
                                "Manus structured output failed: "
                                + str(result.get("error") or "unknown error")
                            )
                        value = result.get("value")
                        if not isinstance(value, dict):
                            raise RuntimeError("Manus structured output is not an object")
                        return (
                            value,
                            task_id,
                            self._task_credit_usage(client, headers, task_id),
                        )

                last_status = _latest_agent_status(events) or last_status
                if last_status == "error":
                    raise RuntimeError(_latest_error(events) or "Manus task failed")
                if last_status == "waiting":
                    # interactive_mode=false 时 Manus 可能短暂发出 waiting，随后会按
                    # best-effort 自动继续；不要过早把正常任务判成失败。
                    logger.info("Manus task is temporarily waiting: %s", task_id)
                    time.sleep(self.poll_interval)
                    continue
                if last_status == "stopped":
                    # Structured extraction can arrive just after the stopped event.
                    stopped_at = stopped_at or time.monotonic()
                    if time.monotonic() - stopped_at > 30:
                        raise RuntimeError(
                            "Manus task stopped without structured output"
                        )
                    time.sleep(min(self.poll_interval, 1.0))
                else:
                    stopped_at = None
                    time.sleep(self.poll_interval)

        raise TimeoutError(
            f"Manus task timed out after {int(self.timeout)} seconds "
            f"(last status: {last_status})"
        )

    def _upload_prompt_file(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        prompt: str,
    ) -> str:
        """Upload a long prompt before task.create and return its file ID."""

        created = self._request_json(
            client,
            "POST",
            "/v2/file.upload",
            headers=headers,
            json_body={"filename": "task-input.txt"},
        )
        file_id = str((created.get("file") or {}).get("id") or "").strip()
        upload_url = str(created.get("upload_url") or "").strip()
        if not file_id or not upload_url:
            raise RuntimeError("Manus file.upload did not return file ID and upload URL")

        payload = str(prompt or "").encode("utf-8")
        response: httpx.Response | Any | None = None
        for attempt in range(1, self.transport_retries + 1):
            try:
                response = client.put(upload_url, content=payload)
                if response.status_code < 500:
                    break
            except httpx.TransportError as exc:
                if attempt >= self.transport_retries:
                    raise ManusTransportError(
                        "Manus 长提示附件上传失败，请稍后重试",
                        safe_to_restart_task=True,
                    ) from exc
            if attempt < self.transport_retries:
                time.sleep(min(2 ** (attempt - 1), 8))
        if response is None or response.status_code >= 400:
            status_code = getattr(response, "status_code", "unknown")
            raise RuntimeError(
                f"Manus prompt attachment upload failed: HTTP {status_code}"
            )

        for attempt in range(1, 11):
            detail = self._request_json(
                client,
                "GET",
                "/v2/file.detail",
                headers=headers,
                params={"file_id": file_id},
            )
            file_detail = dict(detail.get("file") or {})
            status = str(file_detail.get("status") or "").strip().lower()
            if status == "uploaded":
                return file_id
            if status in {"deleted", "error"}:
                raise RuntimeError(
                    "Manus prompt attachment failed: "
                    + str(file_detail.get("error_message") or status)
                )
            if attempt < 10:
                time.sleep(min(0.25 * attempt, 1.0))
        raise TimeoutError("Manus prompt attachment was not ready in time")

    def _task_credit_usage(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        task_id: str,
    ) -> int | None:
        """Read provider-confirmed task Credit; Manus exposes no Token counts."""

        try:
            detail = self._request_json(
                client,
                "GET",
                "/v2/task.detail",
                headers=headers,
                params={"task_id": task_id},
            )
            task = detail.get("task") or {}
            value = task.get("credit_usage") if isinstance(task, dict) else None
            return None if value is None else max(0, int(value))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Manus task %s Credit usage unavailable: %s",
                task_id,
                type(exc).__name__,
            )
            return None

    def _request_json(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_method = str(method or "GET").upper()
        endpoint = path.rsplit("/", 1)[-1]
        min_interval = (
            self.create_min_interval
            if endpoint == "task.create"
            else (
                self.poll_min_interval
                if endpoint == "task.listMessages"
                else 0.0
            )
        )
        max_attempts = self.transport_retries if normalized_method == "GET" else 1
        response: httpx.Response | Any | None = None
        for attempt in range(1, max_attempts + 1):
            _wait_for_request_slot(
                api_base=self.api_base,
                api_key=self.api_key,
                endpoint=endpoint,
                min_interval=min_interval,
            )
            try:
                response = client.request(
                    normalized_method,
                    f"{self.api_base}{path}",
                    headers=headers,
                    json=json_body,
                    params=params,
                )
                break
            except httpx.TransportError as exc:
                if attempt < max_attempts:
                    delay = min(2 ** (attempt - 1), 8)
                    logger.warning(
                        "Manus %s transport interrupted; reconnecting the same "
                        "request in %ss (%s/%s)",
                        endpoint,
                        delay,
                        attempt,
                        max_attempts,
                    )
                    time.sleep(delay)
                    continue
                # A failed GET is safe to repeat inside this method, but once
                # exhausted the surrounding rewrite must not create a new
                # Manus task: the existing asynchronous task may still finish.
                # POST failures are also uncertain because the server may have
                # accepted task.create before the connection was lost.
                raise ManusTransportError(
                    (
                        f"Manus {endpoint} 网络连接中断，已重连 "
                        f"{max_attempts} 次仍未恢复：{exc}"
                    ),
                    safe_to_restart_task=False,
                ) from exc
        if response is None:  # pragma: no cover - defensive invariant
            raise ManusTransportError(
                f"Manus {endpoint} 未返回响应",
                safe_to_restart_task=False,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Manus returned non-JSON response: HTTP {response.status_code}"
            ) from exc
        if response.status_code >= 400 or not data.get("ok", True):
            error = data.get("error") or {}
            code = error.get("code") or response.status_code
            message = error.get("message") or "request failed"
            request_id = (
                data.get("request_id")
                or error.get("request_id")
                or response.headers.get("x-request-id")
                or response.headers.get("x-manus-request-id")
                or ""
            )
            raise ManusAPIError(
                code,
                str(message),
                request_id=str(request_id),
                status_code=response.status_code,
            )
        return data


def _latest_agent_status(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("type") != "status_update":
            continue
        status = event.get("status_update") or {}
        value = status.get("agent_status") or status.get("status")
        if value:
            return str(value)
    return None


def _is_task_not_found(exc: BaseException) -> bool:
    """Return whether Manus reported a temporarily invisible task."""
    message = str(exc).casefold()
    return "not_found" in message and "task not found" in message


def _rewrite_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "body": {
                "type": "string",
                "description": "完整公众号正文，去除空白后不少于2000字，保留自然段和小标题",
            },
            "titles": {
                "type": "array",
                "description": f"{TITLE_CANDIDATE_COUNT}个互不重复的公众号标题",
                "items": {"type": "string"},
            },
            "subtitles": {
                "type": "array",
                "description": (
                    f"{SUBTITLE_CANDIDATE_COUNT}个互不重复、"
                    "用于补充主标题信息的副标题"
                ),
                "items": {"type": "string"},
            },
            "digest": {
                "type": "string",
                "description": ARTICLE_DIGEST_PROMPT,
            },
        },
        "required": ["body", "titles", "subtitles", "digest"],
        "additionalProperties": False,
    }


def _latest_error(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("type") != "error_message":
            continue
        payload = event.get("error_message") or {}
        return str(payload.get("message") or payload.get("content") or "") or None
    return None
