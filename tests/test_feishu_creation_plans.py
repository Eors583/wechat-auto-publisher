from __future__ import annotations

from typing import Any

from app.db import Database
from app.feishu.agent import AgentPlan
from app.feishu.session import FeishuSessionStore
from app.feishu.tool_executor import FeishuToolExecutor
from app.services.creation_plans import (
    BUILTIN_DEFAULT_CREATION_PLAN_ID,
    CreationPlanService,
)


class _Service:
    def __init__(self, db: Database) -> None:
        self.db = db


class _CreationPlans:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, Any]] = []
        self.apply_calls: list[tuple[str, str]] = []
        self.plan = {
            "id": BUILTIN_DEFAULT_CREATION_PLAN_ID,
            "name": "系统默认方案",
            "description": "使用系统默认创作配置",
            "article_prompt_template_name": "系统默认文章提示词",
            "image_prompt_template_name": "系统默认图片提示词",
            "editorial_review_profile_name": "专业深度型",
            "enabled": True,
            "available": True,
            "issues": [],
        }

    def list(
        self,
        *,
        enabled_only: bool = False,
        include_builtin: bool = True,
    ) -> list[dict[str, Any]]:
        self.list_calls.append(
            {
                "enabled_only": enabled_only,
                "include_builtin": include_builtin,
            }
        )
        return [dict(self.plan)]

    def apply_to_account(
        self,
        account_id: str,
        plan_id: str,
    ) -> dict[str, Any]:
        self.apply_calls.append((account_id, plan_id))
        return {
            "account_id": account_id,
            "plan_id": plan_id,
            "plan": dict(self.plan),
            "applied": True,
        }


def _executor(
    tmp_path,
    *,
    admin_open_ids: set[str] | None = None,
) -> tuple[Database, FeishuToolExecutor, _CreationPlans, list[str]]:
    db = Database(tmp_path / "feishu-creation-plans.db")
    db.upsert_official_account(
        {
            "id": "account-1",
            "name": "蓝血研究",
            "app_id": "wx-app",
            "app_secret_encrypted": "encrypted-secret",
            "model_id": "model-1",
            "enabled": True,
        }
    )
    replies: list[str] = []
    executor = FeishuToolExecutor(
        service=_Service(db),  # type: ignore[arg-type]
        config={},
        sessions=FeishuSessionStore(db),
        default_account_ids=[],
        reply_text=lambda _message_id, text: replies.append(str(text)),
        send_text=lambda _chat_id, text: replies.append(str(text)),
        admin_open_ids=admin_open_ids,
    )
    creation_plans = _CreationPlans()
    executor.creation_plans = creation_plans  # type: ignore[assignment]
    return db, executor, creation_plans, replies


def _execute(
    executor: FeishuToolExecutor,
    *,
    tool: str,
    arguments: dict[str, Any],
    text: str,
    open_id: str = "admin-1",
) -> None:
    executor.execute(
        AgentPlan(
            intent=tool,
            analysis_summary="测试飞书创作方案工具",
            tool=tool,
            arguments=arguments,
        ),
        original_text=text,
        message_id=f"message-{tool}",
        chat_id="chat-1",
        open_id=open_id,
        current_batch_id=None,
    )


def test_feishu_lists_available_creation_plans(tmp_path) -> None:
    _db, executor, creation_plans, replies = _executor(tmp_path)

    _execute(
        executor,
        tool="list_creation_plans",
        arguments={},
        text="有哪些创作方案",
    )

    reply = replies[-1]
    assert "系统默认方案" in reply
    assert BUILTIN_DEFAULT_CREATION_PLAN_ID in reply
    assert "系统默认文章提示词" in reply
    assert "专业深度型" in reply
    assert creation_plans.list_calls == [
        {"enabled_only": True, "include_builtin": True}
    ]


def test_apply_creation_plan_requires_confirmation_and_reuses_service(
    tmp_path,
) -> None:
    _db, executor, creation_plans, replies = _executor(
        tmp_path,
        admin_open_ids={"admin-1"},
    )

    arguments = {
        "account_name": "蓝血研究",
        "plan_name": "系统默认方案",
    }
    _execute(
        executor,
        tool="apply_account_creation_plan",
        arguments=arguments,
        text="把蓝血研究切到系统默认方案",
    )

    assert "确认" in replies[-1]
    assert creation_plans.apply_calls == []

    _execute(
        executor,
        tool="apply_account_creation_plan",
        arguments=arguments,
        text="确认给公众号应用创作方案",
    )

    assert creation_plans.apply_calls == [
        ("account-1", BUILTIN_DEFAULT_CREATION_PLAN_ID)
    ]
    assert "蓝血研究已应用创作方案“系统默认方案”" in replies[-1]
    assert "系统默认图片提示词" in replies[-1]
    assert "专业深度型" in replies[-1]


def test_apply_creation_plan_requires_admin_permission(tmp_path) -> None:
    _db, executor, creation_plans, replies = _executor(
        tmp_path,
        admin_open_ids={"admin-1"},
    )

    _execute(
        executor,
        tool="apply_account_creation_plan",
        arguments={
            "account_id": "account-1",
            "plan_id": BUILTIN_DEFAULT_CREATION_PLAN_ID,
        },
        text="确认给公众号应用创作方案",
        open_id="user-2",
    )

    assert "管理员权限" in replies[-1]
    assert creation_plans.apply_calls == []


def test_feishu_creation_plan_tool_integrates_with_real_service(tmp_path) -> None:
    db, executor, _stub, replies = _executor(
        tmp_path,
        admin_open_ids={"admin-1"},
    )
    executor.creation_plans = CreationPlanService(db)

    _execute(
        executor,
        tool="apply_account_creation_plan",
        arguments={
            "account_name": "蓝血研究",
            "plan_name": "系统默认方案",
        },
        text="确认给公众号应用创作方案",
    )

    binding = db.get_account_creation_plan_default("account-1")
    assert binding is not None
    assert binding["creation_plan_id"] == BUILTIN_DEFAULT_CREATION_PLAN_ID
    assert "已应用创作方案“系统默认方案”" in replies[-1]
