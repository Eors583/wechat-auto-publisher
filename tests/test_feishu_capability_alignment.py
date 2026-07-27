from __future__ import annotations

import inspect
from typing import Any

from app.feishu.agent import AgentPlan
from app.feishu.capabilities import (
    BATCH_SERVICE_CAPABILITIES,
    CAPABILITY_BY_SERVICE_METHOD,
    CREATION_PLAN_CAPABILITY_BY_SERVICE_METHOD,
    CREATION_PLAN_SERVICE_CAPABILITIES,
    FeishuSupportStatus,
)
from app.feishu.session import FeishuSessionStore
from app.feishu.tool_catalog import ALLOWED_TOOLS, TOOL_SPECS
from app.feishu.tool_executor import CONFIRMATION_REQUIREMENTS, FeishuToolExecutor
from app.services.batches import BatchService
from app.services.creation_plans import CreationPlanService


class _MemorySessionDb:
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


def test_every_public_batch_service_operation_declares_feishu_support() -> None:
    public_methods = {
        name
        for name, member in inspect.getmembers(BatchService, inspect.isfunction)
        if not name.startswith("_")
    }

    assert set(CAPABILITY_BY_SERVICE_METHOD) == public_methods


def test_every_public_creation_plan_operation_declares_feishu_support() -> None:
    public_methods = {
        name
        for name, member in inspect.getmembers(
            CreationPlanService,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }

    assert set(CREATION_PLAN_CAPABILITY_BY_SERVICE_METHOD) == public_methods


def test_capability_declarations_are_unique_and_actionable() -> None:
    for declarations in (
        BATCH_SERVICE_CAPABILITIES,
        CREATION_PLAN_SERVICE_CAPABILITIES,
    ):
        methods = [item.service_method for item in declarations]
        assert len(methods) == len(set(methods))

        for capability in declarations:
            if capability.status is FeishuSupportStatus.SUPPORTED:
                assert capability.tools, capability.service_method
            elif capability.status in {
                FeishuSupportStatus.PARTIAL,
                FeishuSupportStatus.NOT_APPLICABLE,
            }:
                assert capability.note, capability.service_method


def test_supported_capability_tools_are_registered_and_executable() -> None:
    for declarations in (
        BATCH_SERVICE_CAPABILITIES,
        CREATION_PLAN_SERVICE_CAPABILITIES,
    ):
        for capability in declarations:
            if capability.status is not FeishuSupportStatus.SUPPORTED:
                continue
            for tool in capability.tools:
                assert tool in ALLOWED_TOOLS, (capability.service_method, tool)
                assert tool in TOOL_SPECS, (capability.service_method, tool)
                assert callable(getattr(FeishuToolExecutor, f"_tool_{tool}", None)), (
                    capability.service_method,
                    tool,
                )


def test_creation_plan_application_has_confirmation_contract() -> None:
    capability = CREATION_PLAN_CAPABILITY_BY_SERVICE_METHOD["apply_to_account"]

    assert capability.status is FeishuSupportStatus.SUPPORTED
    assert capability.tools == ("apply_account_creation_plan",)
    assert TOOL_SPECS["apply_account_creation_plan"].requires_confirmation
    assert "apply_account_creation_plan" in CONFIRMATION_REQUIREMENTS


def test_secondary_revision_capabilities_have_full_feishu_contracts() -> None:
    expected = {
        "regenerate_paragraph": "regenerate_paragraph",
        "regenerate_inline_image": "regenerate_inline_image",
        "regenerate_cover": "regenerate_cover",
    }

    for service_method, mutation_tool in expected.items():
        capability = CAPABILITY_BY_SERVICE_METHOD[service_method]
        assert capability.status is FeishuSupportStatus.SUPPORTED
        assert mutation_tool in capability.tools
        assert "instruction" in TOOL_SPECS[mutation_tool].arguments
        assert TOOL_SPECS[mutation_tool].requires_confirmation
        assert mutation_tool in CONFIRMATION_REQUIREMENTS


def test_feishu_secondary_revision_handlers_forward_user_requirements() -> None:
    class RevisionService:
        def __init__(self) -> None:
            self.db = _MemorySessionDb()
            self.calls: list[tuple[Any, ...]] = []

        def regenerate_paragraph(
            self,
            batch_id: str,
            job_id: int,
            paragraph_index: int,
            *,
            instruction: str,
        ) -> dict[str, Any]:
            self.calls.append(
                ("paragraph", batch_id, job_id, paragraph_index, instruction)
            )
            return {"id": job_id, "body": "修改后的段落"}

        def regenerate_cover(
            self,
            batch_id: str,
            job_id: int,
            *,
            instruction: str,
        ) -> dict[str, Any]:
            self.calls.append(("cover", batch_id, job_id, instruction))
            return {"id": job_id, "thumb_media_id": "cover-media-id"}

    service = RevisionService()
    replies: list[str] = []
    executor = FeishuToolExecutor(
        service=service,  # type: ignore[arg-type]
        config={},
        sessions=FeishuSessionStore(service.db),
        default_account_ids=[],
        reply_text=lambda _message_id, text: replies.append(str(text)),
        send_text=lambda _chat_id, text: replies.append(str(text)),
    )

    for tool, arguments, confirmation in (
        (
            "regenerate_paragraph",
            {
                "batch_id": "batch-9",
                "job_id": 12,
                "paragraph_index": 2,
                "instruction": "压缩到一百字并保留关键数据",
            },
            "确认重新生成这个段落",
        ),
        (
            "regenerate_cover",
            {
                "batch_id": "batch-9",
                "job_id": 12,
                "instruction": "突出制造业数字化场景，不要文字",
            },
            "确认重新生成文章封面",
        ),
    ):
        executor.execute(
            AgentPlan(
                intent=tool,
                analysis_summary="验证飞书二次修改",
                tool=tool,
                arguments=arguments,
            ),
            original_text=confirmation,
            message_id=f"message-{tool}",
            chat_id="chat-1",
            open_id="user-1",
            current_batch_id="batch-9",
        )

    assert service.calls == [
        (
            "paragraph",
            "batch-9",
            12,
            2,
            "压缩到一百字并保留关键数据",
        ),
        (
            "cover",
            "batch-9",
            12,
            "突出制造业数字化场景，不要文字",
        ),
    ]
    assert any("第 3 段已重新生成" in reply for reply in replies)
    assert any("cover-media-id" in reply for reply in replies)
