from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from app.feishu.agent import ALLOWED_TOOLS, AgentPlan
from app.feishu.session import FeishuSessionStore
from app.feishu.tool_catalog import TOOL_SPECS
from app.feishu.tool_executor import CONFIRMATION_REQUIREMENTS, FeishuToolExecutor


# These names are the public, stable tool protocol shared by Feishu, the API,
# and the desktop application.  Renaming an internal service method must not
# silently remove one of these tools from the conversational agent.
EXTENDED_TOOL_NAMES = {
    "preflight_accounts",
    "list_batches",
    "list_review_inbox",
    "get_article_attempts",
    "retry_article_step",
    "retry_failed_batch",
    "copy_batch",
    "archive_batch",
    "request_article_changes",
    "update_article_content",
    "move_paragraph",
    "delete_paragraph",
    "regenerate_paragraph",
    "rerender_article",
    "list_article_versions",
    "restore_article_version",
    "regenerate_inline_images",
    "regenerate_inline_image",
    "regenerate_cover",
    "list_cover_options",
    "select_cover",
    "get_article_assets",
    "remove_inline_image",
    "configure_account_images",
    "update_account_layout",
    "list_draft_templates",
    "select_draft_template",
    "list_followed_accounts",
    "import_owned_followed_accounts",
    "save_followed_account",
    "delete_followed_account",
    "refresh_followed_articles",
    "list_followed_articles",
    "update_followed_article",
    "list_topic_sources",
    "save_topic_source",
    "delete_topic_source",
    "refresh_topic_sources",
    "add_manual_topic",
    "list_prompt_templates",
    "save_prompt_template",
    "delete_prompt_template",
    "bind_account_prompt_template",
    "list_models",
    "test_model",
    "generate_model_test_image",
    "get_account_config",
    "set_account_model",
    "test_account_connection",
    "set_official_account_enabled",
    "delete_official_account",
    "confirm_article",
    "list_topics",
    "update_topic_state",
    "load_more_followed_articles",
    "get_wechat_backend_status",
    "set_model_enabled",
    "delete_model",
    "get_feishu_runtime_status",
    "get_operational_overview",
    "list_editorial_review_profiles",
    "save_editorial_review_profile",
    "delete_editorial_review_profile",
    "get_account_editorial_review_default",
    "set_account_editorial_review_default",
    "run_editorial_review",
    "get_editorial_review",
    "generate_editorial_rewrite_candidate",
    "smart_rewrite_from_editorial_review",
    "apply_editorial_review_application",
    "resolve_editorial_review_issue",
}


# A single safety boundary in FeishuToolExecutor.execute must protect these
# actions before their handler is dispatched.  This prevents a newly added
# handler from accidentally omitting its own confirmation check.
CONFIRMATION_EXAMPLES = {
    "retry_article_step": "确认从失败步骤重试文章",
    "retry_failed_batch": "确认重试失败公众号",
    "copy_batch": "确认复制批次重新生成",
    "archive_batch": "确认归档当前批次",
    "update_article_content": "确认保存文章修改",
    "move_paragraph": "确认移动这个段落",
    "delete_paragraph": "确认删除这个段落",
    "regenerate_paragraph": "确认重新生成这个段落",
    "restore_article_version": "确认恢复文章历史版本",
    "regenerate_inline_images": "确认重新生成正文配图",
    "regenerate_inline_image": "确认按要求重新生成这张正文配图",
    "regenerate_cover": "确认重新生成文章封面",
    "select_cover": "确认更换文章封面",
    "remove_inline_image": "确认删除这张正文图片",
    "configure_account_images": "确认修改公众号生图配置",
    "update_account_layout": "确认修改公众号排版",
    "select_draft_template": "确认更换公众号草稿模板",
    "delete_followed_account": "确认删除关注公众号",
    "delete_topic_source": "确认删除选题来源",
    "delete_prompt_template": "确认删除提示词模板",
    "bind_account_prompt_template": "确认更换公众号提示词模板",
    "set_account_model": "确认更换公众号模型",
    "delete_official_account": "确认删除自有公众号",
    "delete_model": "确认删除模型",
    "generate_model_test_image": "确认生成模型测试图",
    "save_editorial_review_profile": "确认保存 AI 评审方案",
    "delete_editorial_review_profile": "确认删除 AI 评审方案",
    "set_account_editorial_review_default": "确认更换公众号默认 AI 评审方案",
    "run_editorial_review": "确认开始 AI 评审",
    "generate_editorial_rewrite_candidate": "确认按 AI 评审建议生成修改稿",
    "smart_rewrite_from_editorial_review": "确认智能修改原文",
    "apply_editorial_review_application": "确认应用 AI 修改稿",
    "resolve_editorial_review_issue": "确认更新 AI 评审核实结果",
}

FORBIDDEN_REMOTE_SECRET_TOOLS = {
    "save_model",
    "save_official_account",
    "test_wechat_backend_login",
    "save_wechat_backend_login",
    "clear_wechat_backend_login",
}


class MemoryContextDb:
    def __init__(self) -> None:
        self.contexts: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, str] = {}

    def get_bot_context(self, scope_id: str) -> dict[str, Any]:
        return dict(self.contexts.get(scope_id) or {})

    def set_bot_context(self, scope_id: str, value: dict[str, Any]) -> None:
        self.contexts[scope_id] = dict(value)

    def get_bot_session(self, scope_id: str) -> str | None:
        return self.sessions.get(scope_id)

    def set_bot_session(self, scope_id: str, batch_id: str) -> None:
        self.sessions[scope_id] = batch_id


def make_executor(service: Any | None = None) -> tuple[FeishuToolExecutor, list[str]]:
    db = getattr(service, "db", None) or MemoryContextDb()
    service = service or SimpleNamespace(db=db, list_accounts=lambda: [])
    replies: list[str] = []
    executor = FeishuToolExecutor(
        service=service,
        config={},
        sessions=FeishuSessionStore(db),
        default_account_ids=[],
        reply_text=lambda _message_id, text: replies.append(str(text)),
        send_text=lambda _chat_id, text: replies.append(str(text)),
    )
    return executor, replies


def run_tool(
    executor: FeishuToolExecutor,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    text: str = "执行",
    batch_id: str | None = "batch-1",
) -> None:
    executor.execute(
        AgentPlan(
            intent=tool,
            analysis_summary="测试工具协议",
            tool=tool,
            arguments=dict(arguments or {}),
        ),
        original_text=text,
        message_id="message-1",
        chat_id="chat-1",
        open_id="user-1",
        current_batch_id=batch_id,
    )


@pytest.mark.parametrize("tool", sorted(EXTENDED_TOOL_NAMES))
def test_extended_tool_is_whitelisted_and_has_handler(tool: str) -> None:
    assert tool in ALLOWED_TOOLS
    assert callable(getattr(FeishuToolExecutor, f"_tool_{tool}", None))


def test_retry_failed_batch_uses_in_place_job_retry() -> None:
    source = inspect.getsource(FeishuToolExecutor._tool_retry_failed_batch)

    assert "self.service.retry_job(" in source
    assert "self.service.retry_failed(" not in source


def test_executor_confirmation_requirements_are_declared_in_tool_catalog() -> None:
    """The planner and deterministic executor must advertise the same guard."""

    missing = {
        tool
        for tool in CONFIRMATION_REQUIREMENTS
        if not TOOL_SPECS[tool].requires_confirmation
    }
    assert missing == set()


def test_executor_rejects_unregistered_tool_even_if_handler_is_injected() -> None:
    executor, _replies = make_executor()
    setattr(executor, "_tool_unregistered", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="未授权"):
        run_tool(executor, "unregistered", batch_id=None)


@pytest.mark.parametrize("tool", sorted(FORBIDDEN_REMOTE_SECRET_TOOLS))
def test_feishu_cannot_read_or_write_sensitive_configuration(tool: str) -> None:
    assert tool not in TOOL_SPECS
    assert tool not in ALLOWED_TOOLS

    executor, _replies = make_executor()
    with pytest.raises(ValueError, match="未授权"):
        run_tool(
            executor,
            tool,
            {
                "api_key": "sk-must-stay-local",
                "app_secret": "wechat-must-stay-local",
                "token": "token-must-stay-local",
                "cookie": "cookie-must-stay-local",
            },
            text="确认保存",
            batch_id=None,
        )


@pytest.mark.parametrize("tool,confirmation", sorted(CONFIRMATION_EXAMPLES.items()))
def test_mutating_tools_are_guarded_before_dispatch(
    tool: str, confirmation: str
) -> None:
    executor, replies = make_executor()
    dispatched: list[dict[str, Any]] = []

    def capture(arguments: dict[str, Any], **_kwargs: Any) -> None:
        dispatched.append(dict(arguments))

    # Replace the concrete handler to prove that the confirmation check lives
    # at the common dispatch boundary, not in only a subset of handlers.
    setattr(executor, f"_tool_{tool}", capture)

    run_tool(executor, tool, {"sentinel": tool}, text="可以，没问题")
    assert dispatched == []
    assert replies and "确认" in replies[-1]

    run_tool(executor, tool, {"sentinel": tool}, text=confirmation)
    assert dispatched == [{"sentinel": tool}]


def test_planner_argument_cannot_bypass_confirmation_boundary() -> None:
    executor, replies = make_executor()
    dispatched: list[dict[str, Any]] = []

    def capture(arguments: dict[str, Any], **_kwargs: Any) -> None:
        dispatched.append(dict(arguments))

    executor._tool_delete_model = capture  # type: ignore[method-assign]
    run_tool(
        executor,
        "delete_model",
        {"model_id": "model-1", "confirmed": True, "confirmation": True},
        text="可以，没问题",
        batch_id=None,
    )

    assert dispatched == []
    assert replies and "确认" in replies[-1]


def test_verified_one_time_code_can_cross_confirmation_boundary() -> None:
    executor, _replies = make_executor()
    dispatched: list[dict[str, Any]] = []

    def capture(arguments: dict[str, Any], **_kwargs: Any) -> None:
        dispatched.append(dict(arguments))

    executor._tool_delete_model = capture  # type: ignore[method-assign]
    plan = AgentPlan(
        intent="delete_model",
        analysis_summary="已校验一次性确认码",
        tool="delete_model",
        arguments={"model_id": "model-1"},
    )
    executor.execute(
        plan,
        original_text="确认 A1B2C3",
        message_id="message-1",
        chat_id="chat-1",
        open_id="user-1",
        current_batch_id=None,
        confirmation_verified=True,
    )

    assert dispatched == [{"model_id": "model-1"}]


def test_batch_read_tools_delegate_filters_to_batch_service() -> None:
    class FakeBatchService:
        db = MemoryContextDb()

        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def list_accounts(self) -> list[dict[str, Any]]:
            return [{"id": "account-1", "name": "蓝血研究", "model_name": "Kimi"}]

        def preflight(
            self, account_ids: list[str], *, deep_model_check: bool = False
        ) -> list[dict[str, Any]]:
            self.calls.append(("preflight", account_ids, deep_model_check))
            return [
                {
                    "account_id": "account-1",
                    "account_name": "蓝血研究",
                    "ready_for_generation": True,
                    "ready_for_draft": True,
                    "checks": [],
                }
            ]

        def list_batches(
            self, *, limit: int = 100, include_archived: bool = False
        ) -> list[dict[str, Any]]:
            self.calls.append(("list_batches", limit, include_archived))
            return [
                {
                    "id": "batch-1",
                    "status": "ready_for_review",
                    "topic": "企业经营",
                    "jobs": [],
                }
            ]

    service = FakeBatchService()
    executor, replies = make_executor(service)

    run_tool(
        executor,
        "preflight_accounts",
        {"account_ids": ["account-1"], "deep_model_check": True},
        batch_id=None,
    )
    run_tool(
        executor,
        "list_batches",
        {"limit": 20, "include_archived": True},
        batch_id=None,
    )

    assert service.calls == [
        ("preflight", ["account-1"], True),
        ("list_batches", 20, True),
    ]
    assert len(replies) == 2
    assert "蓝血研究" in replies[0]
    assert "batch-1" in replies[1]


def test_article_edit_tool_passes_only_explicit_content_fields() -> None:
    class FakeBatchService:
        db = MemoryContextDb()

        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        @staticmethod
        def list_accounts() -> list[dict[str, Any]]:
            return []

        def update_job_content(self, batch_id: str, job_id: int, **changes: Any) -> dict[str, Any]:
            self.calls.append((batch_id, job_id, changes))
            return {
                "id": job_id,
                "account_name": "蓝血研究",
                "selected_title": changes.get("title"),
                "selected_subtitle": changes.get("subtitle"),
                "body": changes.get("body"),
                "digest": changes.get("digest"),
                "status": "ready_for_review",
            }

    service = FakeBatchService()
    executor, replies = make_executor(service)
    run_tool(
        executor,
        "update_article_content",
        {
            "batch_id": "batch-9",
            "job_id": 12,
            "title": "新标题",
            "subtitle": "新副标题",
            "body": "修改后的正文",
            "digest": "新摘要",
            "ignored": "must-not-reach-service",
        },
        text=CONFIRMATION_EXAMPLES["update_article_content"],
    )

    assert service.calls == [
        (
            "batch-9",
            12,
            {
                "title": "新标题",
                "subtitle": "新副标题",
                "body": "修改后的正文",
                "digest": "新摘要",
            },
        )
    ]
    assert replies and "12" in replies[-1]


def test_paragraph_edit_tools_translate_numbers_and_delegate_to_service() -> None:
    class FakeBatchService:
        db = MemoryContextDb()

        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        @staticmethod
        def list_accounts() -> list[dict[str, Any]]:
            return []

        def move_paragraph(
            self,
            batch_id: str,
            job_id: int,
            paragraph_index: int,
            target_index: int,
        ) -> dict[str, Any]:
            self.calls.append(
                ("move", batch_id, job_id, paragraph_index, target_index)
            )
            return {"id": job_id, "body": "第二段\n\n第一段"}

        def delete_paragraph(
            self, batch_id: str, job_id: int, paragraph_index: int
        ) -> dict[str, Any]:
            self.calls.append(("delete", batch_id, job_id, paragraph_index))
            return {"id": job_id, "body": "第一段"}

    service = FakeBatchService()
    executor, replies = make_executor(service)
    run_tool(
        executor,
        "move_paragraph",
        {
            "batch_id": "batch-9",
            "job_id": 12,
            "paragraph_number": 2,
            "direction": "up",
        },
        text=CONFIRMATION_EXAMPLES["move_paragraph"],
    )
    run_tool(
        executor,
        "delete_paragraph",
        {"batch_id": "batch-9", "job_id": 12, "paragraph_index": 1},
        text=CONFIRMATION_EXAMPLES["delete_paragraph"],
    )

    assert service.calls == [
        ("move", "batch-9", 12, 1, 0),
        ("delete", "batch-9", 12, 1),
    ]
    assert any("段落已移动" in reply for reply in replies)
    assert any("段落已删除" in reply for reply in replies)


def test_single_image_revision_tool_passes_index_and_instruction() -> None:
    class FakeBatchService:
        db = MemoryContextDb()

        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        @staticmethod
        def list_accounts() -> list[dict[str, Any]]:
            return []

        def regenerate_inline_image(
            self,
            batch_id: str,
            job_id: int,
            image_index: int,
            *,
            instruction: str,
        ) -> dict[str, Any]:
            self.calls.append((batch_id, job_id, image_index, instruction))
            return {
                "id": job_id,
                "meta": {
                    "inline_images": [
                        {
                            "index": image_index,
                            "url": "https://example.com/revised.jpg",
                        }
                    ]
                },
            }

    service = FakeBatchService()
    executor, replies = make_executor(service)
    run_tool(
        executor,
        "regenerate_inline_image",
        {
            "batch_id": "batch-9",
            "job_id": 12,
            "image_index": 2,
            "instruction": "改成供应链仓库现场",
        },
        text=CONFIRMATION_EXAMPLES["regenerate_inline_image"],
    )

    assert service.calls == [("batch-9", 12, 2, "改成供应链仓库现场")]
    assert any("配图 2 已按要求重新生成" in reply for reply in replies)


def test_paragraph_revision_tool_translates_number_and_delegates_instruction() -> None:
    class FakeBatchService:
        db = MemoryContextDb()

        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        @staticmethod
        def list_accounts() -> list[dict[str, Any]]:
            return []

        def regenerate_paragraph(
            self,
            batch_id: str,
            job_id: int,
            paragraph_index: int,
            *,
            instruction: str,
        ) -> dict[str, Any]:
            self.calls.append((batch_id, job_id, paragraph_index, instruction))
            return {"id": job_id, "body": "Revised paragraph"}

    service = FakeBatchService()
    executor, replies = make_executor(service)
    run_tool(
        executor,
        "regenerate_paragraph",
        {
            "batch_id": "batch-9",
            "job_id": 12,
            "paragraph_number": 3,
            "instruction": "Make the conclusion more concrete",
        },
        text=CONFIRMATION_EXAMPLES["regenerate_paragraph"],
    )

    assert service.calls == [
        ("batch-9", 12, 2, "Make the conclusion more concrete")
    ]
    assert len(replies) == 2
    assert "Revised paragraph" in replies[-1]


def test_paragraph_revision_tool_accepts_zero_based_index() -> None:
    class FakeBatchService:
        db = MemoryContextDb()

        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        @staticmethod
        def list_accounts() -> list[dict[str, Any]]:
            return []

        def regenerate_paragraph(
            self,
            batch_id: str,
            job_id: int,
            paragraph_index: int,
            *,
            instruction: str,
        ) -> dict[str, Any]:
            self.calls.append((batch_id, job_id, paragraph_index, instruction))
            return {"id": job_id, "body": "First paragraph revised"}

    service = FakeBatchService()
    executor, _replies = make_executor(service)
    run_tool(
        executor,
        "regenerate_paragraph",
        {
            "batch_id": "batch-9",
            "job_id": 12,
            "paragraph_index": 0,
            "instruction": "Shorten it",
        },
        text=CONFIRMATION_EXAMPLES["regenerate_paragraph"],
    )

    assert service.calls == [("batch-9", 12, 0, "Shorten it")]


def test_bulk_images_cover_and_remove_tools_delegate_all_parameters() -> None:
    class FakeBatchService:
        db = MemoryContextDb()

        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        @staticmethod
        def list_accounts() -> list[dict[str, Any]]:
            return []

        def regenerate_inline_images(
            self, batch_id: str, job_id: int
        ) -> dict[str, Any]:
            self.calls.append(("all-images", batch_id, job_id))
            return {
                "id": job_id,
                "meta": {"inline_images": [{"index": 1}, {"index": 2}]},
            }

        def regenerate_cover(
            self,
            batch_id: str,
            job_id: int,
            *,
            instruction: str,
        ) -> dict[str, Any]:
            self.calls.append(("cover", batch_id, job_id, instruction))
            return {"id": job_id, "thumb_media_id": "cover-media-id"}

        def remove_inline_image(
            self, batch_id: str, job_id: int, image_index: int
        ) -> dict[str, Any]:
            self.calls.append(("remove-image", batch_id, job_id, image_index))
            return {"id": job_id}

    service = FakeBatchService()
    executor, replies = make_executor(service)
    run_tool(
        executor,
        "regenerate_inline_images",
        {"batch_id": "batch-9", "job_id": 12},
        text=CONFIRMATION_EXAMPLES["regenerate_inline_images"],
    )
    run_tool(
        executor,
        "regenerate_cover",
        {
            "batch_id": "batch-9",
            "job_id": 12,
            "instruction": "Use a restrained boardroom scene",
        },
        text=CONFIRMATION_EXAMPLES["regenerate_cover"],
    )
    run_tool(
        executor,
        "remove_inline_image",
        {"batch_id": "batch-9", "job_id": 12, "image_index": 2},
        text=CONFIRMATION_EXAMPLES["remove_inline_image"],
    )

    assert service.calls == [
        ("all-images", "batch-9", 12),
        ("cover", "batch-9", 12, "Use a restrained boardroom scene"),
        ("remove-image", "batch-9", 12, 2),
    ]
    assert len(replies) == 5


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (
            "regenerate_paragraph",
            {"batch_id": "batch-9", "job_id": 12, "paragraph_number": 1},
        ),
        (
            "regenerate_paragraph",
            {
                "batch_id": "batch-9",
                "job_id": 12,
                "paragraph_number": 0,
                "instruction": "Rewrite",
            },
        ),
        (
            "regenerate_inline_image",
            {
                "batch_id": "batch-9",
                "job_id": 12,
                "image_index": 1,
                "instruction": "   ",
            },
        ),
        (
            "regenerate_inline_image",
            {
                "batch_id": "batch-9",
                "job_id": 12,
                "image_index": 0,
                "instruction": "Use a factory scene",
            },
        ),
        (
            "remove_inline_image",
            {"batch_id": "batch-9", "job_id": 12},
        ),
    ],
)
def test_revision_tools_reject_missing_or_invalid_parameters_before_service(
    tool: str,
    arguments: dict[str, Any],
) -> None:
    class FailIfCalledService:
        db = MemoryContextDb()

        @staticmethod
        def list_accounts() -> list[dict[str, Any]]:
            return []

        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"service method must not be called: {name}")

    executor, replies = make_executor(FailIfCalledService())
    run_tool(
        executor,
        tool,
        arguments,
        text=CONFIRMATION_EXAMPLES[tool],
    )

    assert len(replies) == 1


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (
            "regenerate_paragraph",
            {
                "batch_id": "batch-9",
                "job_id": 12,
                "paragraph_number": 1,
                "instruction": "Shorten it",
            },
        ),
        (
            "regenerate_inline_images",
            {"batch_id": "batch-9", "job_id": 12},
        ),
        (
            "regenerate_inline_image",
            {
                "batch_id": "batch-9",
                "job_id": 12,
                "image_index": 1,
                "instruction": "Use a warehouse scene",
            },
        ),
        (
            "regenerate_cover",
            {
                "batch_id": "batch-9",
                "job_id": 12,
                "instruction": "Use a restrained boardroom scene",
            },
        ),
        (
            "remove_inline_image",
            {"batch_id": "batch-9", "job_id": 12, "image_index": 1},
        ),
    ],
)
def test_successful_secondary_revision_reopens_previously_reviewed_job(
    tool: str,
    arguments: dict[str, Any],
) -> None:
    class SuccessfulRevisionService:
        db = MemoryContextDb()

        @staticmethod
        def list_accounts() -> list[dict[str, Any]]:
            return []

        @staticmethod
        def _job() -> dict[str, Any]:
            return {
                "id": 12,
                "account_name": "Account A",
                "body": "Revised body",
                "thumb_media_id": "cover-media-id",
                "meta": {
                    "inline_images": [
                        {"index": 1, "url": "https://example.com/image.jpg"}
                    ]
                },
            }

        def regenerate_paragraph(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return self._job()

        def regenerate_inline_images(
            self, *_args: Any, **_kwargs: Any
        ) -> dict[str, Any]:
            return self._job()

        def regenerate_inline_image(
            self, *_args: Any, **_kwargs: Any
        ) -> dict[str, Any]:
            return self._job()

        def regenerate_cover(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return self._job()

        def remove_inline_image(
            self, *_args: Any, **_kwargs: Any
        ) -> dict[str, Any]:
            return self._job()

    executor, _replies = make_executor(SuccessfulRevisionService())
    executor.sessions.start_review(
        "chat-1",
        {
            "jobs": [
                {
                    "id": 12,
                    "account_name": "Account A",
                    "status": "ready_for_review",
                },
                {
                    "id": 13,
                    "account_name": "Account B",
                    "status": "ready_for_review",
                },
            ]
        },
    )
    executor.sessions.mark_reviewed("chat-1", 12)
    assert executor.sessions.review_state("chat-1") == {
        "queue": [
            {"job_id": 12, "account_name": "Account A"},
            {"job_id": 13, "account_name": "Account B"},
        ],
        "reviewed_job_ids": [12],
        "current_review_job_id": 13,
        "completed": 1,
        "total": 2,
    }

    run_tool(
        executor,
        tool,
        arguments,
        text=CONFIRMATION_EXAMPLES[tool],
    )

    state = executor.sessions.review_state("chat-1")
    assert state["reviewed_job_ids"] == []
    assert state["current_review_job_id"] == 12
    assert state["completed"] == 0
    assert state["total"] == 2


def test_revision_service_error_is_not_reported_as_success() -> None:
    class FailingBatchService:
        db = MemoryContextDb()

        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        @staticmethod
        def list_accounts() -> list[dict[str, Any]]:
            return []

        def regenerate_inline_image(
            self,
            batch_id: str,
            job_id: int,
            image_index: int,
            *,
            instruction: str,
        ) -> dict[str, Any]:
            self.calls.append((batch_id, job_id, image_index, instruction))
            raise RuntimeError("image provider rate limited")

    service = FailingBatchService()
    executor, replies = make_executor(service)
    executor.sessions.start_review(
        "chat-1",
        {
            "jobs": [
                {
                    "id": 12,
                    "account_name": "Account A",
                    "status": "ready_for_review",
                },
                {
                    "id": 13,
                    "account_name": "Account B",
                    "status": "ready_for_review",
                },
            ]
        },
    )
    executor.sessions.mark_reviewed("chat-1", 12)

    with pytest.raises(RuntimeError, match="rate limited"):
        run_tool(
            executor,
            "regenerate_inline_image",
            {
                "batch_id": "batch-9",
                "job_id": 12,
                "image_index": 2,
                "instruction": "Use a warehouse scene",
            },
            text=CONFIRMATION_EXAMPLES["regenerate_inline_image"],
        )

    assert service.calls == [
        ("batch-9", 12, 2, "Use a warehouse scene")
    ]
    # Only the in-progress message was emitted; no false success was sent.
    assert len(replies) == 1
    state = executor.sessions.review_state("chat-1")
    assert state["reviewed_job_ids"] == [12]
    assert state["current_review_job_id"] == 13


def test_followed_content_read_tools_use_followed_content_service() -> None:
    class FakeFollowedContent:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def list_accounts(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
            self.calls.append(("accounts", enabled_only))
            return [{"id": "follow-1", "name": "管理评论", "enabled": True}]

        def list_articles(self, **filters: Any) -> list[dict[str, Any]]:
            self.calls.append(("articles", filters))
            return [
                {
                    "id": "article-1",
                    "account_name": "管理评论",
                    "title": "复杂项目如何交付",
                    "url": "https://mp.weixin.qq.com/s/example",
                    "published_at": "2026-07-21T08:00:00+00:00",
                }
            ]

    executor, replies = make_executor()
    followed = FakeFollowedContent()
    executor.followed_content = followed

    run_tool(
        executor,
        "list_followed_accounts",
        {"enabled_only": True},
        batch_id=None,
    )
    run_tool(
        executor,
        "list_followed_articles",
        {
            "account_ids": ["follow-1"],
            "days": 30,
            "keyword": "项目",
            "unread_only": True,
            "limit": 25,
        },
        batch_id=None,
    )

    assert followed.calls == [
        ("accounts", True),
        (
            "articles",
            {
                "account_ids": ["follow-1"],
                "days": 30,
                "keyword": "项目",
                "unread_only": True,
                "favorite_only": False,
                "unrewritten_only": False,
                "include_ignored": False,
                "limit": 25,
            },
        ),
    ]
    assert "管理评论" in replies[0]
    assert "复杂项目如何交付" in replies[1]
    assert "https://mp.weixin.qq.com/s/example" in replies[1]


def test_topic_source_read_tools_use_topic_source_service() -> None:
    class FakeTopicSources:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def list_sources(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
            self.calls.append(("list", enabled_only))
            return [
                {
                    "id": "source-1",
                    "name": "36氪",
                    "source_type": "rss",
                    "enabled": True,
                }
            ]

        def refresh(self, source_ids: list[str] | None = None) -> dict[str, Any]:
            self.calls.append(("refresh", source_ids))
            return {
                "total": 3,
                "sources": [
                    {"source_id": "source-1", "name": "36氪", "count": 3, "error": ""}
                ],
            }

    executor, replies = make_executor()
    sources = FakeTopicSources()
    executor.topic_sources = sources

    run_tool(executor, "list_topic_sources", {"enabled_only": True}, batch_id=None)
    run_tool(
        executor,
        "refresh_topic_sources",
        {"source_ids": ["source-1"]},
        batch_id=None,
    )

    assert sources.calls == [("list", True), ("refresh", ["source-1"])]
    assert "36氪" in replies[0]
    assert "3" in replies[1]


def test_review_inbox_and_step_retry_tools_share_batch_service() -> None:
    class RecoveryService:
        db = MemoryContextDb()

        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        @staticmethod
        def list_accounts() -> list[dict[str, Any]]:
            return []

        def list_review_inbox(self, **filters: Any) -> dict[str, Any]:
            self.calls.append(("inbox", filters))
            return {
                "counts": {
                    "review": 2,
                    "write_failed": 1,
                    "generation_failed": 1,
                    "today_completed": 3,
                },
                "items": [
                    {
                        "batch_id": "batch-9",
                        "batch_display_id": "20260728-01",
                        "job_id": 12,
                        "account_name": "蓝血研究",
                        "title": "经营系统如何落地",
                        "body_chars": 2300,
                        "priority_reason": "超过24小时未审核",
                        "blockers": ["待核实一处事实"],
                    }
                ],
                "next_cursor": None,
            }

        def list_job_attempts(
            self, batch_id: str, job_id: int
        ) -> list[dict[str, Any]]:
            self.calls.append(("attempts", batch_id, job_id))
            return [
                {
                    "stage": "rewrite",
                    "attempt_no": 2,
                    "status": "failed",
                    "error_code": "rewrite.invalid_argument",
                    "started_at": "2026-07-28T09:00:00+00:00",
                    "completed_at": "2026-07-28T09:00:30+00:00",
                }
            ]

        def retry_job(self, batch_id: str, job_id: int, **options: Any) -> dict[str, Any]:
            self.calls.append(("retry", batch_id, job_id, options))
            return {
                "status": "accepted",
                "job": {
                    "id": job_id,
                    "account_name": "蓝血研究",
                    "status": "rewriting",
                },
            }

    service = RecoveryService()
    executor, replies = make_executor(service)

    run_tool(
        executor,
        "list_review_inbox",
        {"bucket": "review", "limit": 10},
        batch_id=None,
    )
    run_tool(
        executor,
        "get_article_attempts",
        {"batch_id": "batch-9", "job_id": 12},
    )
    run_tool(
        executor,
        "retry_article_step",
        {
            "batch_id": "batch-9",
            "job_id": 12,
            "step": "rewrite",
            "model_id": "model-backup",
        },
        text=CONFIRMATION_EXAMPLES["retry_article_step"],
    )

    assert service.calls == [
        (
            "inbox",
            {
                "bucket": "review",
                "account_id": None,
                "limit": 10,
                "cursor": None,
            },
        ),
        ("attempts", "batch-9", 12),
        (
            "retry",
            "batch-9",
            12,
            {
                "step": "rewrite",
                "model_id": "model-backup",
                "source_url": None,
                "raw_content": None,
            },
        ),
    ]
    assert "待我审核收件箱" in replies[0]
    assert "经营系统如何落地" in replies[0]
    assert "待核实一处事实" in replies[0]
    assert "rewrite.invalid_argument" in replies[1]
    assert "2026-07-28T09:00:30+00:00" in replies[1]
    assert "恢复请求已提交" in replies[-1]
