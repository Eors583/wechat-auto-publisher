from __future__ import annotations

import base64
import unittest
from typing import Any
from unittest.mock import patch

import httpx

from app.ai.failover import FailoverRewriter
from app.ai.manus import (
    ManusAPIError,
    ManusClient,
    ManusTransportError,
    _message_content,
    _next_request_at,
    _wait_for_request_slot,
    is_non_retryable_manus_error,
    _is_task_not_found,
    _latest_agent_status,
    _latest_error,
    _rewrite_schema,
)


class ManusClientTests(unittest.TestCase):
    def tearDown(self) -> None:
        _next_request_at.clear()

    def test_latest_status_and_error_are_parsed(self) -> None:
        events = [
            {
                "type": "status_update",
                "status_update": {"agent_status": "stopped"},
            },
            {
                "type": "error_message",
                "error_message": {"message": "failed"},
            },
        ]
        self.assertEqual(_latest_agent_status(events), "stopped")
        self.assertEqual(_latest_error(events), "failed")

    def test_long_prompt_moves_to_attachment_without_losing_content(self) -> None:
        prompt = "完整原稿与改写要求" * 1000

        content = _message_content(prompt)

        self.assertIsInstance(content, list)
        parts = content if isinstance(content, list) else []
        self.assertLess(len(parts[0]["text"]), 4000)
        encoded = parts[1]["file_data"].split(",", 1)[1]
        restored = base64.b64decode(encoded).decode()
        self.assertEqual(restored, prompt)
        self.assertEqual(parts[1]["filename"], "task-input.txt")

    def test_short_prompt_keeps_plain_text_message(self) -> None:
        self.assertEqual(_message_content("短请求"), "短请求")

    def test_task_not_found_is_recognized_case_insensitively(self) -> None:
        self.assertTrue(
            _is_task_not_found(
                RuntimeError("Manus API error not_found: Task not found")
            )
        )
        self.assertFalse(
            _is_task_not_found(RuntimeError("Manus API error forbidden: denied"))
        )

    def test_rewrite_schema_contains_all_single_pass_fields(self) -> None:
        schema = _rewrite_schema()
        self.assertEqual(schema["required"], ["body", "titles", "subtitles"])
        self.assertEqual(set(schema["properties"]), {"body", "titles", "subtitles"})
        self.assertFalse(schema["additionalProperties"])

    def test_every_structured_output_schema_avoids_manus_forbidden_keywords(
        self,
    ) -> None:
        """Manus accepts only a restricted JSON Schema vocabulary.

        Candidate counts remain prompt/quality-check requirements; encoding
        them as array constraints makes task.create reject the whole request.
        """

        client = ManusClient("test-key")
        captured: list[tuple[str, dict[str, Any]]] = []
        titles = [f"主标题{index}" for index in range(1, 11)]
        subtitles = [f"副标题{index}" for index in range(1, 11)]

        def fake_run(
            _prompt: str,
            schema: dict[str, Any],
            *,
            title: str,
        ) -> dict[str, Any]:
            captured.append((title, schema))
            if title == "公众号内容生成":
                return {"text": "OK"}
            if title == "公众号标题优化":
                return {"titles": titles}
            return {
                "body": "完整正文",
                "titles": titles,
                "subtitles": subtitles,
            }

        with patch.object(client, "_run_structured_task", side_effect=fake_run):
            client.rewrite("改写要求")
            client.expand_rewrite("话题", "待扩写正文")
            client.complete("只回复 OK")
            client.complete_json(
                "返回对象",
                {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                    "additionalProperties": False,
                },
                title="公众号评审结果",
            )
            client.optimize_titles("优化标题")

        self.assertEqual(
            {title for title, _schema in captured},
            {
                "公众号文章改写",
                "公众号文章扩写",
                "公众号内容生成",
                "公众号评审结果",
                "公众号标题优化",
            },
        )

        forbidden = {
            "pattern",
            "format",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "multipleOf",
            "minItems",
            "maxItems",
            "uniqueItems",
            "allOf",
            "oneOf",
            "not",
            "if",
            "then",
            "else",
        }

        def find_forbidden(value: Any, path: str = "$") -> list[str]:
            found: list[str] = []
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}"
                    if key in forbidden:
                        found.append(child_path)
                    found.extend(find_forbidden(child, child_path))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    found.extend(find_forbidden(child, f"{path}[{index}]"))
            return found

        violations = {
            title: find_forbidden(schema)
            for title, schema in captured
            if find_forbidden(schema)
        }
        self.assertEqual(violations, {})

    def test_api_error_preserves_request_id_for_support_diagnostics(self) -> None:
        class FakeResponse:
            status_code = 400

            @staticmethod
            def json() -> dict[str, Any]:
                return {
                    "ok": False,
                    "error": {
                        "code": "invalid_argument",
                        "message": "structured schema is invalid",
                    },
                    "request_id": "request-safe-123",
                }

        class FakeHttpClient:
            @staticmethod
            def request(*_args: Any, **_kwargs: Any) -> FakeResponse:
                return FakeResponse()

        client = ManusClient("test-key")
        with self.assertRaisesRegex(
            RuntimeError,
            r"invalid_argument.*request-safe-123",
        ):
            client._request_json(  # noqa: SLF001 - transport contract regression
                FakeHttpClient(),  # type: ignore[arg-type]
                "POST",
                "/v2/task.create",
                headers={"x-manus-api-key": "redacted"},
                json_body={"safe": True},
            )

    @patch("app.ai.manus.time.sleep", return_value=None)
    def test_poll_transport_drop_retries_same_request(
        self,
        sleep_mock,
    ) -> None:
        class FakeResponse:
            status_code = 200
            headers: dict[str, str] = {}

            @staticmethod
            def json() -> dict[str, Any]:
                return {"ok": True, "messages": []}

        class FlakyClient:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
                self.calls += 1
                if self.calls < 3:
                    raise httpx.ReadError("connection reset while polling")
                return FakeResponse()

        transport = FlakyClient()
        client = ManusClient(
            "poll-retry-key",
            poll_min_interval=0,
            transport_retries=4,
        )
        result = client._request_json(  # noqa: SLF001 - transport regression
            transport,  # type: ignore[arg-type]
            "GET",
            "/v2/task.listMessages",
            headers={"x-manus-api-key": "redacted"},
            params={"task_id": "task-existing"},
        )

        self.assertEqual(result, {"ok": True, "messages": []})
        self.assertEqual(transport.calls, 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_uncertain_task_create_drop_is_not_blindly_retried(self) -> None:
        class BrokenClient:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, *_args: Any, **_kwargs: Any) -> None:
                self.calls += 1
                raise httpx.RemoteProtocolError(
                    "server disconnected without sending a response"
                )

        transport = BrokenClient()
        client = ManusClient(
            "create-drop-key",
            create_min_interval=0,
            transport_retries=4,
        )
        with self.assertRaises(ManusTransportError) as raised:
            client._request_json(  # noqa: SLF001 - transport regression
                transport,  # type: ignore[arg-type]
                "POST",
                "/v2/task.create",
                headers={"x-manus-api-key": "redacted"},
                json_body={"message": {"content": "test"}},
            )

        self.assertEqual(transport.calls, 1)
        self.assertTrue(is_non_retryable_manus_error(raised.exception))

    @patch("app.ai.manus.time.sleep", return_value=None)
    @patch("app.ai.manus.time.monotonic", side_effect=[100.0, 100.0, 106.25])
    def test_same_credential_task_creation_is_staggered(
        self,
        _monotonic_mock,
        sleep_mock,
    ) -> None:
        common = {
            "api_base": "https://api.manus.ai",
            "api_key": "shared-key",
            "endpoint": "task.create",
            "min_interval": 6.25,
        }
        _wait_for_request_slot(**common)
        _wait_for_request_slot(**common)

        sleep_mock.assert_called_once_with(6.25)

    @patch("app.ai.failover.time.sleep", return_value=None)
    def test_invalid_argument_is_not_retried_by_failover(self, _sleep) -> None:
        rewriter = FailoverRewriter(
            {
                "ai": {
                    "primary": "manus",
                    "fallback": "manus",
                    "max_retries_per_model": 3,
                    "min_body_chars": 2000,
                    "max_similarity": 0.99,
                    "manus": {
                        "api_key": "test-key",
                        "api_base": "https://api.manus.ai",
                        "model": "manus-1.6",
                    },
                }
            }
        )

        class InvalidRequestManus:
            def __init__(self) -> None:
                self.calls = 0

            def rewrite(self, _prompt: str) -> None:
                self.calls += 1
                raise ManusAPIError(
                    "invalid_argument",
                    "rejected request",
                    request_id="request-safe-456",
                )

        client = InvalidRequestManus()
        rewriter._clients = {"manus": client}

        with self.assertRaisesRegex(
            RuntimeError,
            r"invalid_argument.*request-safe-456",
        ):
            rewriter.rewrite("话题", "参考材料")

        self.assertEqual(client.calls, 1)
        _sleep.assert_not_called()

    @patch("app.ai.manus.time.sleep", return_value=None)
    def test_new_task_visibility_delay_retries_same_task(self, _sleep) -> None:
        client = ManusClient("test-key", timeout=60, poll_interval=0)
        responses = iter(
            [
                {"ok": True, "task_id": "task-123"},
                RuntimeError("Manus API error not_found: task not found"),
                RuntimeError("Manus API error not_found: Task not found"),
                {
                    "ok": True,
                    "messages": [
                        {
                            "type": "structured_output_result",
                            "structured_output_result": {
                                "success": True,
                                "value": {"text": "done"},
                            },
                        }
                    ],
                },
            ]
        )
        requested_task_ids: list[str] = []

        def fake_request(*_args, **kwargs):
            if kwargs.get("params"):
                requested_task_ids.append(kwargs["params"]["task_id"])
            response = next(responses)
            if isinstance(response, BaseException):
                raise response
            return response

        with patch.object(client, "_request_json", side_effect=fake_request):
            result = client._run_structured_task(
                "test", {"type": "object"}, title="test"
            )

        self.assertEqual(result, {"text": "done"})
        self.assertEqual(requested_task_ids, ["task-123"] * 3)


if __name__ == "__main__":
    unittest.main()
