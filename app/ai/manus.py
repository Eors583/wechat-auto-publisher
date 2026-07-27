from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from . import (
    EMPHASIS_PROMPT,
    RewriteResult,
    SUBTITLE_CANDIDATE_COUNT,
    TITLE_CANDIDATE_COUNT,
    TitleResult,
    parse_rewrite_output,
    parse_title_output,
)

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.poll_interval = poll_interval

    def rewrite(self, prompt: str) -> RewriteResult:
        single_pass_prompt = (
            "请直接基于提供的材料，在一次任务中完成正文、标题和副标题，不要联网研究，"
            "不要调用浏览器、终端、代码、文件或其他工具，也不要把标题生成拆成后续任务。\n"
            "body 去除空白后不得少于 2000 字，不设置严格字数上限，也不需要精确统计或"
            f"反复截断字数；titles 必须给出恰好 {TITLE_CANDIDATE_COUNT} 个互不重复的"
            f"标题；subtitles 必须给出恰好 {SUBTITLE_CANDIDATE_COUNT} 个互不重复的"
            "副标题，并与标题形成信息互补。该数量规则优先于运营提示词中的旧数量。\n"
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
            f"{SUBTITLE_CANDIDATE_COUNT} 个副标题。不要解释任务，不要省略正文。\n\n"
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
        if not self.api_key:
            raise RuntimeError("MANUS_API_KEY is empty")

        headers = {
            "x-manus-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "message": {"content": prompt},
            "agent_profile": self.model,
            "interactive_mode": False,
            "hide_in_task_list": True,
            "share_visibility": "private",
            "title": title,
            "structured_output_schema": schema,
        }

        deadline = time.monotonic() + self.timeout
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
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
                        return value

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
        response = client.request(
            method,
            f"{self.api_base}{path}",
            headers=headers,
            json=json_body,
            params=params,
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
        },
        "required": ["body", "titles", "subtitles"],
        "additionalProperties": False,
    }


def _latest_error(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("type") != "error_message":
            continue
        payload = event.get("error_message") or {}
        return str(payload.get("message") or payload.get("content") or "") or None
    return None
