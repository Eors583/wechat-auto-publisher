from __future__ import annotations

from typing import Any

import pytest

from app.feishu.agent import AgentPlan
from app.feishu.session import FeishuSessionStore
from app.feishu.tool_executor import (
    ADMIN_TOOLS,
    BATCH_SCOPED_TOOLS,
    CONFIRMATION_REQUIREMENTS,
    FeishuToolExecutor,
)


class MemoryDb:
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


def sample_review() -> dict[str, Any]:
    return {
        "id": "review-real-1",
        "batch_id": "batch-1",
        "job_id": 12,
        "profile_id": "professional_depth",
        "profile_name": "专业深度型",
        "model_name": "Kimi",
        "status": "completed",
        "blocking_count": 1,
        "result": {
            "overall_score": 82,
            "summary": "文章结构清楚，但事实依据和行动建议仍需加强。",
            "dimensions": [
                {"name": "标题", "score": 80, "comment": "准确但吸引力一般"},
                {"name": "开头", "score": 76, "comment": "进入主题稍慢"},
                {"name": "预计完读率", "score": 72, "comment": "中段节奏可收紧"},
                {"name": "点赞意愿", "score": 78, "comment": "观点有认同空间"},
                {"name": "转发动机", "score": 70, "comment": "缺少可转发价值锚点"},
            ],
            "issues": [
                {
                    "id": "issue-real-fact",
                    "role_name": "事实核查",
                    "severity": "high",
                    "problem": "关键数据缺少可核验来源。",
                    "suggestion": "运营人员核对原始来源。",
                    "can_auto_apply": False,
                    "resolution": "open",
                },
                {
                    "id": "issue-real-editor",
                    "role_name": "主编",
                    "severity": "medium",
                    "problem": "结尾缺少明确行动建议。",
                    "suggestion": "补充三条可执行建议。",
                    "can_auto_apply": True,
                    "resolution": "open",
                },
            ],
        },
    }


class EditorialService:
    def __init__(self) -> None:
        self.db = MemoryDb()
        self.calls: list[tuple[Any, ...]] = []
        self.review = sample_review()
        self.application = {
            "id": "application-real-1",
            "review_id": "review-real-1",
            "status": "candidate_ready",
            "candidate_snapshot": {
                "title": "新标题",
                "body": "新正文",
                "change_summary": "补充了行动建议",
            },
        }

    def get_editorial_review_options(self) -> dict[str, Any]:
        return {
            "roles": [
                {"id": "chief_editor", "name": "主编"},
                {"id": "fact_checker", "name": "事实核查"},
            ],
            "styles": [{"id": "rigorous", "name": "严谨专业"}],
        }

    def list_editorial_review_profiles(
        self, *, include_builtin: bool = True
    ) -> list[dict[str, Any]]:
        assert include_builtin is True
        return [
            {
                "id": "professional_depth",
                "name": "专业深度型",
                "builtin": True,
                "enabled": True,
                "config": {
                    "role_ids": ["chief_editor", "fact_checker"],
                    "style_ids": ["rigorous"],
                    "strictness": "standard",
                },
            }
        ]

    def set_account_editorial_review_default(
        self,
        account_id: str,
        *,
        profile_id: str,
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self.calls.append(("set-default", account_id, profile_id, config))
        return {
            "account_id": account_id,
            "account_name": "公众号A",
            "profile_id": profile_id,
            "profile_name": "专业深度型",
            "config": config or {},
        }

    def run_editorial_review(
        self,
        batch_id: str,
        job_id: int,
        *,
        profile_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("run", batch_id, job_id, profile_id, config))
        return dict(self.review)

    def get_editorial_review(self, review_id: str) -> dict[str, Any]:
        assert review_id == "review-real-1"
        return dict(self.review)

    def list_editorial_reviews(self, **_filters: Any) -> list[dict[str, Any]]:
        return [dict(self.review)]

    def generate_editorial_rewrite_candidate(
        self,
        batch_id: str,
        job_id: int,
        review_id: str,
        *,
        issue_ids: list[str],
        rewrite_mode: str,
        paragraph_numbers: list[int] | None,
        instruction: str,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "generate",
                batch_id,
                job_id,
                review_id,
                issue_ids,
                rewrite_mode,
                paragraph_numbers,
                instruction,
            )
        )
        return {**self.review, "application": dict(self.application)}

    def list_editorial_review_applications(
        self, _review_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        assert limit == 20
        return [dict(self.application)]

    def get_editorial_review_application(
        self, application_id: str
    ) -> dict[str, Any]:
        assert application_id == "application-real-1"
        return dict(self.application)

    def apply_editorial_review_application(
        self, batch_id: str, job_id: int, application_id: str
    ) -> dict[str, Any]:
        self.calls.append(("apply", batch_id, job_id, application_id))
        return {"id": job_id, "account_name": "公众号A"}

    def resolve_editorial_review_issue(
        self,
        review_id: str,
        issue_id: str,
        *,
        resolution: str,
        note: str,
        resolved_by: str,
    ) -> dict[str, Any]:
        self.calls.append(
            ("resolve", review_id, issue_id, resolution, note, resolved_by)
        )
        result = dict(self.review)
        result["blocking_count"] = 0
        return result


def make_executor(
    service: EditorialService,
) -> tuple[FeishuToolExecutor, list[str], FeishuSessionStore]:
    replies: list[str] = []
    sessions = FeishuSessionStore(service.db)
    executor = FeishuToolExecutor(
        service=service,  # type: ignore[arg-type]
        config={},
        sessions=sessions,
        default_account_ids=[],
        reply_text=lambda _message_id, text: replies.append(str(text)),
        send_text=lambda _chat_id, text: replies.append(str(text)),
    )
    return executor, replies, sessions


def execute(
    executor: FeishuToolExecutor,
    tool: str,
    arguments: dict[str, Any],
    confirmation: str,
) -> None:
    executor.execute(
        AgentPlan(
            intent=tool,
            analysis_summary="测试 AI 评审工具",
            tool=tool,
            arguments=arguments,
        ),
        original_text=confirmation,
        message_id=f"message-{tool}",
        chat_id="chat-1",
        open_id="user-1",
        current_batch_id="batch-1",
    )


def test_editorial_review_tool_guards_and_scopes_are_declared() -> None:
    admin_tools = {
        "save_editorial_review_profile",
        "delete_editorial_review_profile",
        "set_account_editorial_review_default",
    }
    batch_tools = {
        "run_editorial_review",
        "get_editorial_review",
        "generate_editorial_rewrite_candidate",
        "smart_rewrite_from_editorial_review",
        "apply_editorial_review_application",
        "resolve_editorial_review_issue",
    }
    confirmed_tools = {
        "save_editorial_review_profile",
        "delete_editorial_review_profile",
        "set_account_editorial_review_default",
        "run_editorial_review",
        "generate_editorial_rewrite_candidate",
        "smart_rewrite_from_editorial_review",
        "apply_editorial_review_application",
        "resolve_editorial_review_issue",
    }

    assert admin_tools <= ADMIN_TOOLS
    assert batch_tools <= BATCH_SCOPED_TOOLS
    assert confirmed_tools <= set(CONFIRMATION_REQUIREMENTS)


def test_issue_numbers_are_mapped_to_server_ids_before_candidate_generation() -> None:
    service = EditorialService()
    executor, replies, sessions = make_executor(service)

    execute(
        executor,
        "run_editorial_review",
        {"batch_id": "batch-1", "job_id": 12},
        "确认开始 AI 评审",
    )
    execute(
        executor,
        "generate_editorial_rewrite_candidate",
        {
            "batch_id": "batch-1",
            "job_id": 12,
            "issue_numbers": [2],
            "rewrite_mode": "selected_issues",
            "instruction": "保持专业克制",
        },
        "确认按 AI 评审建议生成修改稿",
    )

    generate_call = next(item for item in service.calls if item[0] == "generate")
    assert generate_call[4] == ["issue-real-editor"]
    assert sessions.get("chat-1")["current_editorial_review_application_id"] == (
        "application-real-1"
    )
    assert any("尚未覆盖原稿" in reply for reply in replies)
    assert any("候选正文预览" in reply and "新正文" in reply for reply in replies)
    assert any("查看当前文章" in reply for reply in replies)


def test_feishu_review_injects_engagement_focus_and_formats_five_dimensions() -> None:
    service = EditorialService()
    executor, replies, _sessions = make_executor(service)

    execute(
        executor,
        "run_editorial_review",
        {
            "batch_id": "batch-1",
            "job_id": 12,
            "focus": "保持公众号的专业定位",
        },
        "确认开始 AI 评审",
    )

    run_call = next(item for item in service.calls if item[0] == "run")
    config = run_call[4]
    assert "标题吸引力" in config["focus"]
    assert "预计完读率" in config["focus"]
    assert "公众号补充重点：保持公众号的专业定位" in config["focus"]
    assert len(config["required_checks"]) == 5
    assert "最多给出 5 条" in config["advanced_rules"]
    rendered = "\n".join(replies)
    assert "本轮重点：标题、开头、预计完读率、点赞意愿、转发动机" in rendered
    assert "预计完读率：72" in rendered
    assert "整体改进建议（请按编号选择是否接受）" in rendered


def test_smart_rewrite_uses_visible_issue_numbers_and_applies_in_one_step() -> None:
    service = EditorialService()
    executor, replies, sessions = make_executor(service)
    executor._remember_editorial_review("chat-1", service.review)  # noqa: SLF001
    sessions.update(
        "chat-1",
        review_queue=[{"job_id": 12, "account_name": "公众号A"}],
        reviewed_job_ids=[12],
        current_review_job_id=None,
    )

    execute(
        executor,
        "smart_rewrite_from_editorial_review",
        {
            "batch_id": "batch-1",
            "job_id": 12,
            "issue_numbers": [2],
        },
        "确认智能修改原文，接受第 2 条建议",
    )

    generate_call = next(item for item in service.calls if item[0] == "generate")
    assert generate_call[4] == ["issue-real-editor"]
    assert generate_call[5:] == ("engagement_optimization", None, "")
    assert ("apply", "batch-1", 12, "application-real-1") in service.calls
    state = sessions.review_state("chat-1")
    assert state["reviewed_job_ids"] == []
    assert state["current_review_job_id"] == 12
    assert any("已原位回到“已查看，未确认”" in reply for reply in replies)


def test_smart_rewrite_requires_at_least_one_visible_issue_number() -> None:
    service = EditorialService()
    executor, _replies, _sessions = make_executor(service)
    executor._remember_editorial_review("chat-1", service.review)  # noqa: SLF001

    with pytest.raises(ValueError, match="至少一条"):
        execute(
            executor,
            "smart_rewrite_from_editorial_review",
            {"batch_id": "batch-1", "job_id": 12},
            "确认智能修改原文",
        )

    assert not any(item[0] in {"generate", "apply"} for item in service.calls)


def test_profile_number_is_mapped_before_setting_account_default() -> None:
    service = EditorialService()
    executor, _replies, _sessions = make_executor(service)
    executor._one_account = lambda _args: {  # type: ignore[method-assign]
        "id": "account-a",
        "name": "公众号A",
    }

    execute(
        executor,
        "list_editorial_review_profiles",
        {},
        "列出 AI 评审方案",
    )
    execute(
        executor,
        "set_account_editorial_review_default",
        {
            "account_name": "公众号A",
            "profile_number": 1,
            "strictness": "strict",
        },
        "确认更换公众号默认 AI 评审方案",
    )

    assert (
        "set-default",
        "account-a",
        "professional_depth",
        {"strictness": "strict"},
    ) in service.calls


def test_applying_candidate_reopens_shared_review_state() -> None:
    service = EditorialService()
    executor, _replies, sessions = make_executor(service)
    sessions.update(
        "chat-1",
        current_editorial_review_id="review-real-1",
        current_editorial_review_application_id="application-real-1",
        editorial_review_applications=[
            {
                "number": 1,
                "id": "application-real-1",
                "review_id": "review-real-1",
            }
        ],
        review_queue=[{"job_id": 12, "account_name": "公众号A"}],
        reviewed_job_ids=[12],
        current_review_job_id=None,
    )

    execute(
        executor,
        "apply_editorial_review_application",
        {
            "batch_id": "batch-1",
            "job_id": 12,
            "application_number": 1,
        },
        "确认应用 AI 修改稿",
    )

    assert ("apply", "batch-1", 12, "application-real-1") in service.calls
    state = sessions.review_state("chat-1")
    assert state["reviewed_job_ids"] == []
    assert state["current_review_job_id"] == 12


def test_issue_resolution_uses_visible_number_and_actor_open_id() -> None:
    service = EditorialService()
    executor, _replies, sessions = make_executor(service)
    executor._remember_editorial_review("chat-1", service.review)  # noqa: SLF001

    execute(
        executor,
        "resolve_editorial_review_issue",
        {
            "batch_id": "batch-1",
            "job_id": 12,
            "issue_number": 1,
            "resolution": "已核实",
            "note": "已对照原始公告",
        },
        "确认更新 AI 评审核实结果",
    )

    assert (
        "resolve",
        "review-real-1",
        "issue-real-fact",
        "resolved",
        "已对照原始公告",
        "user-1",
    ) in service.calls
    assert sessions.get("chat-1")["current_editorial_review_id"] == "review-real-1"
