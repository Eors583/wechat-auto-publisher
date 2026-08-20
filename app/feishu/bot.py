from __future__ import annotations

import logging
import hmac
import re
import threading
from pathlib import Path
from typing import Any

from app.feishu.agent import AgentPlan, FeishuToolAgent
from app.feishu.constants import HELP_TEXT
from app.feishu.events import parse_message_event
from app.feishu.gateway import FeishuGateway
from app.feishu.legacy import LegacyCommandHandler
from app.feishu.pairing import consume_pairing_code
from app.feishu.presenter import (
    format_article_preview,
    format_draft_result,
    format_review,
    format_status,
)
from app.feishu.progress import FeishuProgressReporter
from app.feishu.runtime import update_runtime, utc_now
from app.feishu.session import FeishuSessionStore
from app.feishu.tool_executor import FeishuToolExecutor
from app.services import BatchService
from app.services.failures import sanitize_failure_text


logger = logging.getLogger("uvicorn.error")


class FeishuBot:
    """Thin orchestrator joining transport, planning, sessions and tools."""

    def __init__(self, config: dict[str, Any], service: BatchService) -> None:
        self.config = config
        self.service = service
        self.feishu = dict(config.get("feishu") or {})
        self.app_id = str(self.feishu.get("app_id") or "").strip()
        self.app_secret = str(self.feishu.get("app_secret") or "").strip()
        if not self.app_id or not self.app_secret:
            raise ValueError("飞书已启用，但 feishu.app_id / app_secret 未配置")

        self.integration_id = str(self.feishu.get("integration_id") or "").strip()
        self.owner_user_id = str(self.feishu.get("owner_user_id") or "").strip()
        self.bound_open_id = str(self.feishu.get("bound_open_id") or "").strip()
        self.allowed_open_ids = {
            str(item).strip()
            for item in self.feishu.get("allowed_open_ids") or []
            if str(item).strip()
        }
        # Legacy broad/group authorization is deliberately ignored. The
        # multi-tenant path below uses one exact bound Open ID.
        self.allowed_chat_ids: set[str] = set()
        self.allow_all = False
        self.default_account_ids = [
            str(item).strip()
            for item in self.feishu.get("default_account_ids") or []
            if str(item).strip()
        ]
        self.allowed_account_ids = [
            str(item).strip()
            for item in self.feishu.get("allowed_account_ids") or []
            if str(item).strip()
        ]
        self.agent_model_id = str(self.feishu.get("agent_model_id") or "").strip()

        self.sessions = FeishuSessionStore(
            service.db, integration_id=self.integration_id
        )
        self.progress_reporter = FeishuProgressReporter()
        self.gateway = FeishuGateway(self.app_id, self.app_secret, self.feishu)
        self.agent = (
            FeishuToolAgent(service.db, config, self.agent_model_id)
            if self.agent_model_id
            else None
        )
        self.tool_executor = FeishuToolExecutor(
            service=service,
            config=config,
            sessions=self.sessions,
            default_account_ids=self.default_account_ids,
            reply_text=self._reply_text,
            send_text=self._send_text,
            send_image=self._send_image,
            admin_open_ids=set(self.allowed_open_ids),
            integration_id=self.integration_id,
            allowed_account_ids=self.allowed_account_ids,
        )
        self.legacy = LegacyCommandHandler(
            sessions=self.sessions,
            executor=self.tool_executor,
            reply_text=self._reply_text,
        )
        service.add_listener(self._on_batch_changed)

    def start(self) -> None:
        integration_id = getattr(self, "integration_id", "")
        update_runtime(
            self.service.db,
            integration_id=integration_id,
            status="connecting",
            app_id=self.app_id,
            started_at=utc_now(),
            last_message_at="",
            last_reply_at="",
            last_reply_error_at="",
            last_chat_id="",
            last_open_id="",
            last_error="",
        )
        try:
            self.gateway.start(self._on_message_event)
        except Exception as exc:
            update_runtime(
                self.service.db,
                integration_id=integration_id,
                status="error",
                last_error=sanitize_failure_text(exc),
            )
            raise

    def _on_message_event(self, data: Any) -> None:
        threading.Thread(
            target=self._handle_message,
            args=(data,),
            daemon=True,
            name="feishu-message-handler",
        ).start()

    def _handle_message(self, data: Any) -> None:
        integration_id = getattr(self, "integration_id", "")
        try:
            message = parse_message_event(data)
            update_runtime(
                self.service.db,
                integration_id=integration_id,
                status="running",
                app_id=self.app_id,
                last_message_at=utc_now(),
                last_chat_id=message.chat_id,
                last_open_id=message.open_id,
                last_error="",
            )
            if integration_id and message.app_id != self.app_id:
                logger.warning("Rejected Feishu event with mismatched app_id")
                return
            if integration_id and message.chat_type != "p2p":
                self._reply_text(
                    message.message_id,
                    "当前机器人仅支持绑定用户私聊，群聊消息不会执行任何操作。",
                )
                return
            claimed = (
                self.service.db.claim_feishu_event(
                    integration_id, message.event_id
                )
                if integration_id
                else self.service.db.claim_event(message.event_id)
            )
            if not message.event_id or not claimed:
                return
            logger.info(
                "Feishu event claimed: event_id=%s message_id=%s chat_id=%s",
                message.event_id,
                message.message_id,
                message.chat_id,
            )
            if not self._authorized(message.open_id, message.chat_id):
                if message.message_type == "text" and consume_pairing_code(
                    self.service.db,
                    text=message.text,
                    open_id=message.open_id,
                    chat_id=message.chat_id,
                    integration_id=integration_id,
                ):
                    self.bound_open_id = message.open_id
                    self.allowed_open_ids.add(message.open_id)
                    self.allow_all = False
                    if self.tool_executor.admin_open_ids is not None:
                        self.tool_executor.admin_open_ids.add(message.open_id)
                    self._reply_text(
                        message.message_id,
                        "绑定成功。你已经可以使用公众号内容机器人，"
                        "请继续发送“帮助”或一篇文章链接。",
                    )
                    return
                self._reply_text(
                    message.message_id,
                    "当前飞书账号没有使用这个专属机器人的权限。请由机器人所属用户在网页中重新生成配对码。",
                )
                return
            if message.message_type != "text":
                self._reply_text(message.message_id, "目前只支持文本消息，请直接发送文章链接。")
                return
            logger.info(
                "Feishu text event received: event_id=%s chat_id=%s",
                message.event_id,
                message.chat_id,
            )
            self._dispatch_text(
                message.text,
                message.message_id,
                message.chat_id,
                message.open_id,
            )
        except Exception as exc:  # noqa: BLE001
            safe_error = sanitize_failure_text(exc)
            update_runtime(
                self.service.db,
                integration_id=integration_id,
                last_error=safe_error,
            )
            logger.error("Feishu message handling failed: %s", safe_error)

    def _dispatch_text(
        self, text: str, message_id: str, chat_id: str, open_id: str
    ) -> None:
        pending = self.sessions.pending_action(chat_id)
        normalized = "".join(str(text or "").split())
        if pending and any(word in normalized for word in ("取消操作", "取消确认", "不要执行")):
            self.sessions.clear_pending_action(chat_id)
            self._reply_text(message_id, "待确认操作已取消。")
            return
        confirmed = self.sessions.confirm_pending_action(chat_id, text)
        if confirmed:
            arguments = dict(confirmed.get("arguments") or {})
            self._reply_text(
                message_id,
                f'确认码有效，开始执行：{confirmed.get("prompt") or confirmed.get("tool")}',
            )
            self.tool_executor.execute(
                AgentPlan(
                    intent="执行已确认操作",
                    analysis_summary="使用会话中保存的参数快照",
                    steps=["校验一次性确认码", "执行已保存工具"],
                    tool=str(confirmed["tool"]),
                    arguments=arguments,
                ),
                original_text=text,
                message_id=message_id,
                chat_id=chat_id,
                open_id=open_id,
                current_batch_id=self.sessions.current_batch_id(chat_id),
                confirmation_verified=True,
            )
            return
        if pending and "确认" in normalized:
            self._reply_text(
                message_id,
                f'确认码不正确或已过期，请回复“确认 {pending.get("code")}”；也可以回复“取消操作”。',
            )
            return
        planning_text, sensitive_fields = _redact_sensitive_fields(text)
        if sensitive_fields:
            self._reply_text(
                message_id,
                "为了保护密钥，本条消息中的敏感配置没有保存，也不会交给智能体。"
                "请在本机桌面端“设置”中配置 API Key、AppSecret、"
                "微信公众号后台登录态或微信云中转接入码。",
            )
            return
        if not self.agent:
            self.legacy.dispatch(text, message_id, chat_id, open_id)
            return
        batch_id = self.sessions.current_batch_id(chat_id)
        current_batch = None
        if batch_id:
            try:
                current_batch = self.service.get_batch(batch_id)
            except KeyError:
                pass
        try:
            plan = self.agent.plan(
                planning_text,
                accounts=self.service.list_accounts(),
                current_batch=current_batch,
                recent_hot_topics=self.sessions.recent_hot_topics(chat_id),
                review_state=self.sessions.review_state(chat_id),
            )
        except Exception as exc:  # noqa: BLE001
            safe_error = sanitize_failure_text(
                _redact_known_values(str(exc), sensitive_fields)
            )
            logger.error("Feishu agent planning failed: %s", safe_error)
            self._reply_text(
                message_id,
                f"智能体分析失败，本次没有执行任何操作：{safe_error}\n\n"
                f"可以按以下固定指令重试：\n{HELP_TEXT}",
            )
            return

        logger.info(
            "Feishu agent plan: model=%s tool=%s intent=%s",
            self.agent_model_id,
            plan.tool,
            plan.intent,
        )
        try:
            if plan.tool != "chat":
                self._reply_text(message_id, plan.plan_text)
            self.tool_executor.execute(
                plan,
                original_text=text,
                message_id=message_id,
                chat_id=chat_id,
                open_id=open_id,
                current_batch_id=batch_id,
            )
        except Exception as exc:  # noqa: BLE001
            safe_error = sanitize_failure_text(
                _redact_known_values(str(exc), sensitive_fields)
            )
            logger.error(
                "Feishu tool execution failed: tool=%s error=%s",
                plan.tool,
                safe_error,
            )
            self._reply_text(
                message_id,
                f"操作执行失败（{plan.tool}）：{safe_error}",
            )

    def _on_batch_changed(self, batch: dict[str, Any]) -> None:
        integration_id = getattr(self, "integration_id", "")
        if integration_id and str(
            batch.get("source_integration_id") or ""
        ) != integration_id:
            return
        if integration_id:
            current = self.service.db.get_feishu_integration()
            if not (
                current
                and bool(current.get("enabled"))
                and str(current.get("bound_open_id") or "")
                == getattr(self, "bound_open_id", "")
            ):
                return
        chat_id = str(batch.get("chat_id") or "")
        if not chat_id:
            return
        status = str(batch.get("status") or "")
        jobs = list(batch.get("jobs") or [])
        ready_jobs = [
            job
            for job in jobs
            if str(job.get("status") or "") == "ready_for_review"
        ]
        if status in {"processing", "injecting"}:
            progress_text = self.progress_reporter.render_if_changed(chat_id, batch)
            if progress_text:
                self._send_text(chat_id, progress_text)
        elif status == "ready_for_review" or (
            status == "partial_failed" and ready_jobs
        ):
            first = self.sessions.start_review(chat_id, batch)
            if status == "partial_failed":
                failed_count = sum(
                    1
                    for job in jobs
                    if str(job.get("status") or "") in {"failed", "cancelled"}
                )
                self._send_text(
                    chat_id,
                    f"本批次部分任务未完成：{len(ready_jobs)} 篇文章已生成，"
                    f"{failed_count} 篇失败或已停止。先审核已生成文章，"
                    "失败任务可稍后单独重试。",
                )
            self._send_text(
                chat_id,
                format_review({**batch, "jobs": ready_jobs}),
            )
            if first:
                first_job = next(
                    (
                        job
                        for job in jobs
                        if int(job["id"]) == int(first["job_id"])
                    ),
                    None,
                )
                if first_job:
                    mark_viewed = getattr(self.service, "mark_job_viewed", None)
                    if callable(mark_viewed):
                        try:
                            first_job = mark_viewed(
                                str(batch.get("id") or ""),
                                int(first_job["id"]),
                            )
                        except (KeyError, ValueError):
                            logger.warning(
                                "Could not mark Feishu preview as viewed: "
                                "batch_id=%s job_id=%s",
                                batch.get("id"),
                                first_job.get("id"),
                                exc_info=True,
                            )
                    self._send_text(chat_id, format_article_preview(first_job))
        elif status in {"drafted", "partial_failed"}:
            self.sessions.update(chat_id, stage=status)
            self._send_text(chat_id, format_draft_result(batch))

    def _authorized(self, open_id: str, chat_id: str) -> bool:
        if getattr(self, "integration_id", ""):
            bound_open_id = getattr(self, "bound_open_id", "")
            return bool(bound_open_id) and hmac.compare_digest(
                str(open_id or ""), bound_open_id
            )
        return open_id in self.allowed_open_ids

    def _reply_text(self, message_id: str, text: str) -> None:
        try:
            self.gateway.reply_text(message_id, text)
            update_runtime(
                self.service.db,
                integration_id=getattr(self, "integration_id", ""),
                last_reply_at=utc_now(),
                last_error="",
            )
        except Exception as exc:
            update_runtime(
                self.service.db,
                integration_id=getattr(self, "integration_id", ""),
                last_reply_error_at=utc_now(),
                last_error=sanitize_failure_text(exc),
            )
            raise

    def _send_text(self, chat_id: str, text: str) -> None:
        try:
            self.gateway.send_text(chat_id, text)
            update_runtime(
                self.service.db,
                integration_id=getattr(self, "integration_id", ""),
                last_reply_at=utc_now(),
                last_error="",
            )
        except Exception as exc:
            update_runtime(
                self.service.db,
                integration_id=getattr(self, "integration_id", ""),
                last_reply_error_at=utc_now(),
                last_error=sanitize_failure_text(exc),
            )
            raise

    def _send_image(
        self,
        chat_id: str,
        image: str | Path | bytes | bytearray | memoryview,
        *,
        file_name: str | None = None,
    ) -> str:
        """Callback used by Feishu tools to upload and send an image."""

        try:
            image_key = self.gateway.send_image(chat_id, image, file_name=file_name)
            update_runtime(
                self.service.db,
                integration_id=getattr(self, "integration_id", ""),
                last_reply_at=utc_now(),
                last_error="",
            )
            return image_key
        except Exception as exc:
            update_runtime(
                self.service.db,
                integration_id=getattr(self, "integration_id", ""),
                last_reply_error_at=utc_now(),
                last_error=sanitize_failure_text(exc),
            )
            raise

    # Public callback for independently developed tool modules.
    send_image = _send_image

    # Compatibility for callers that previously used this formatter on FeishuBot.
    _format_status = staticmethod(format_status)


_SENSITIVE_VALUE = re.compile(
    r"(?i)(api[\s_-]*key|app[\s_-]*secret|wechat[\s_-]*token|token)"
    r"\s*[\"']?\s*[:=：]\s*[\"']?([^\s\"',，；}]+)"
)
_COOKIE_VALUE = re.compile(r"(?im)(cookie)\s*[:=：]\s*([^\r\n]+)")


def _redact_sensitive_fields(text: str) -> tuple[str, dict[str, str]]:
    """Keep API keys and AppSecrets out of the planning model prompt."""

    values: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        label = re.sub(r"[\s_-]+", "_", match.group(1).lower())
        if label == "app_secret":
            field = "app_secret"
        elif label in {"token", "wechat_token"}:
            field = "token"
        else:
            field = "api_key"
        values[field] = match.group(2).strip()
        return f"{field}=[已安全提取，不要要求用户重复发送]"

    def replace_cookie(match: re.Match[str]) -> str:
        values["cookie"] = match.group(2).strip().strip("\"'")
        return "cookie=[已安全提取，不要要求用户重复发送]"

    redacted = _COOKIE_VALUE.sub(replace_cookie, text or "")
    return _SENSITIVE_VALUE.sub(replace, redacted), values


def _redact_known_values(text: str, values: dict[str, str]) -> str:
    result = str(text or "")
    for value in values.values():
        secret = str(value or "").strip()
        if secret:
            result = result.replace(secret, "[已隐藏]")
    return result
