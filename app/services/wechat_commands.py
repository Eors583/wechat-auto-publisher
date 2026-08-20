from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Any

from app.accounts import apply_account_selection
from app.ai.model_registry import decrypt_api_key, encrypt_api_key
from app.db import Database
from app.services.failures import sanitize_failure_text
from app.wechat.factory import build_wechat_client

PUBLIC_API_BASE_URL = "https://api.bluebloodlab.cn"
SETTING_PREFIX = "wechat.command."
PAIRING_PATTERN = re.compile(r"^绑定\s*([0-9]{8})$")
logger = logging.getLogger(__name__)


class WeChatCommandService:
    """Account-scoped settings and execution for Official Account commands."""

    def __init__(self, db: Database, config: dict[str, Any]) -> None:
        if not db.owner_user_id:
            raise ValueError("微信指挥服务必须绑定登录用户")
        self.db = db
        self.config = config

    def public_settings(self, account_id: str) -> dict[str, Any]:
        account = self._account(account_id)
        stored = self._load(account_id)
        return {
            "enabled": bool(stored.get("enabled", False)),
            "has_token": bool(stored.get("token_encrypted")),
            "has_encoding_aes_key": bool(stored.get("encoding_aes_key_encrypted")),
            "allowed_open_ids": list(stored.get("allowed_open_ids") or []),
            "callback_url": self.callback_url(account_id),
            "account_name": str(account.get("name") or ""),
            "app_id": str(account.get("app_id") or ""),
        }

    def provision(self, account_id: str) -> dict[str, Any]:
        """Create a fresh callback Token and EncodingAESKey for one account."""

        self._account(account_id)
        token = secrets.token_hex(16)
        encoding_aes_key = "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(43)
        )
        stored = self._load(account_id)
        stored.update(
            enabled=True,
            token_encrypted=encrypt_api_key(token),
            encoding_aes_key_encrypted=encrypt_api_key(encoding_aes_key),
        )
        self._save(account_id, stored)
        return {
            **self.public_settings(account_id),
            "token": token,
            "encoding_aes_key": encoding_aes_key,
        }

    def set_enabled(self, account_id: str, enabled: bool) -> dict[str, Any]:
        stored = self._load(account_id)
        if enabled and not (
            stored.get("token_encrypted") and stored.get("encoding_aes_key_encrypted")
        ):
            raise ValueError("请先生成微信回调 Token 和 EncodingAESKey")
        stored["enabled"] = bool(enabled)
        self._save(account_id, stored)
        return self.public_settings(account_id)

    def create_pairing_code(self, account_id: str) -> str:
        self._account(account_id)
        stored = self._load(account_id)
        if not bool(stored.get("enabled", False)):
            raise ValueError("请先生成接入参数并启用微信指挥")
        code = f"{secrets.randbelow(100_000_000):08d}"
        stored["pairing"] = {
            "code_hash": hashlib.sha256(code.encode()).hexdigest(),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(
                timespec="seconds"
            ),
        }
        self._save(account_id, stored)
        return f"绑定 {code}"

    def authorize_or_pair(
        self,
        account_id: str,
        *,
        open_id: str,
        text: str,
    ) -> tuple[bool, bool]:
        stored = self._load(account_id)
        allowed = {
            str(item).strip()
            for item in stored.get("allowed_open_ids") or []
            if str(item).strip()
        }
        if open_id in allowed:
            return True, False
        match = PAIRING_PATTERN.fullmatch(str(text or "").strip())
        pairing = stored.get("pairing")
        if not match or not isinstance(pairing, dict):
            return False, False
        try:
            expires_at = datetime.fromisoformat(str(pairing.get("expires_at") or ""))
        except ValueError:
            expires_at = datetime.min.replace(tzinfo=UTC)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        supplied_hash = hashlib.sha256(match.group(1).encode()).hexdigest()
        if expires_at <= datetime.now(UTC) or not secrets.compare_digest(
            supplied_hash,
            str(pairing.get("code_hash") or ""),
        ):
            return False, False
        allowed.add(open_id)
        stored["allowed_open_ids"] = sorted(allowed)
        stored.pop("pairing", None)
        self._save(account_id, stored)
        return True, True

    def effective_settings(self, account_id: str) -> dict[str, Any]:
        account = self._account(account_id)
        stored = self._load(account_id)
        token_encrypted = str(stored.get("token_encrypted") or "")
        aes_key_encrypted = str(stored.get("encoding_aes_key_encrypted") or "")
        return {
            "enabled": bool(stored.get("enabled", False)),
            "token": decrypt_api_key(token_encrypted) if token_encrypted else "",
            "encoding_aes_key": (
                decrypt_api_key(aes_key_encrypted) if aes_key_encrypted else ""
            ),
            "allowed_open_ids": list(stored.get("allowed_open_ids") or []),
            "app_id": str(account.get("app_id") or ""),
        }

    def callback_url(self, account_id: str) -> str:
        origin = str(
            os.getenv("WECHAT_COMMAND_PUBLIC_BASE_URL") or PUBLIC_API_BASE_URL
        ).rstrip("/")
        return (
            f"{origin}/api/v1/wechat/commands/"
            f"{self.db.owner_user_id}/{str(account_id).strip()}"
        )

    def run_command(self, account_id: str, open_id: str, text: str) -> None:
        """Plan and execute one authorized message, replying through WeChat."""

        from app.feishu.agent import AgentPlan, FeishuToolAgent
        from app.feishu.bot import _redact_known_values, _redact_sensitive_fields
        from app.feishu.legacy import LegacyCommandHandler
        from app.feishu.presenter import (
            format_article_preview,
            format_draft_result,
            format_review,
        )
        from app.feishu.progress import FeishuProgressReporter
        from app.feishu.session import FeishuSessionStore
        from app.feishu.tool_executor import FeishuToolExecutor
        from app.services.batches import BatchService

        service = BatchService(
            self.config,
            owner_user_id=self.db.owner_user_id,
            recover_stale_work=False,
        )
        account_config, account = apply_account_selection(
            self.config,
            service.db,
            account_id,
        )
        client = build_wechat_client(
            account_config,
            service.db,
            str((account_config.get("wechat") or {}).get("app_id") or ""),
            str((account_config.get("wechat") or {}).get("app_secret") or ""),
        )

        def send_message(_target: str, content: str) -> None:
            client.send_customer_service_text(open_id, str(content or ""))

        scope_id = f"wechat:{self.db.owner_user_id}:{account_id}:{open_id}"
        sessions = FeishuSessionStore(service.db)
        progress_reporter = FeishuProgressReporter()

        def notify_batch_changed(batch: dict[str, Any]) -> None:
            if str(batch.get("chat_id") or "") != scope_id:
                return
            batch_status = str(batch.get("status") or "")
            jobs = list(batch.get("jobs") or [])
            ready_jobs = [
                job
                for job in jobs
                if str(job.get("status") or "") == "ready_for_review"
            ]
            if batch_status in {"processing", "injecting"}:
                progress = progress_reporter.render_if_changed(scope_id, batch)
                if progress:
                    send_message(scope_id, progress)
                return
            if batch_status == "ready_for_review" or (
                batch_status == "partial_failed" and ready_jobs
            ):
                first = sessions.start_review(scope_id, {**batch, "jobs": ready_jobs})
                send_message(scope_id, format_review({**batch, "jobs": ready_jobs}))
                if first:
                    first_job = next(
                        (
                            job
                            for job in ready_jobs
                            if int(job["id"]) == int(first["job_id"])
                        ),
                        None,
                    )
                    if first_job:
                        send_message(scope_id, format_article_preview(first_job))
                return
            if batch_status in {"drafted", "partial_failed"}:
                sessions.update(scope_id, stage=batch_status)
                send_message(scope_id, format_draft_result(batch))

        service.add_listener(notify_batch_changed)
        executor = FeishuToolExecutor(
            service=service,
            config=account_config,
            sessions=sessions,
            default_account_ids=[account_id],
            reply_text=send_message,
            send_text=send_message,
            admin_open_ids={open_id},
            source_channel="wechat",
            channel_settings_label="微信指挥设置",
        )
        legacy = LegacyCommandHandler(
            sessions=sessions,
            executor=executor,
            reply_text=send_message,
        )
        model_id = str(
            (self.config.get("feishu") or {}).get("agent_model_id")
            or account.get("_effective_model_id")
            or ""
        ).strip()
        agent = (
            FeishuToolAgent(service.db, account_config, model_id) if model_id else None
        )
        message_id = f"wechat:{open_id}"

        try:
            pending = sessions.pending_action(scope_id)
            normalized = "".join(str(text or "").split())
            if pending and any(
                word in normalized for word in ("取消操作", "取消确认", "不要执行")
            ):
                sessions.clear_pending_action(scope_id)
                send_message(message_id, "待确认操作已取消。")
                return
            confirmed = sessions.confirm_pending_action(scope_id, text)
            if confirmed:
                executor.execute(
                    AgentPlan(
                        intent="执行已确认操作",
                        analysis_summary="使用会话中保存的参数快照",
                        steps=["校验一次性确认码", "执行已保存工具"],
                        tool=str(confirmed["tool"]),
                        arguments=dict(confirmed.get("arguments") or {}),
                    ),
                    original_text=text,
                    message_id=message_id,
                    chat_id=scope_id,
                    open_id=open_id,
                    current_batch_id=sessions.current_batch_id(scope_id),
                    confirmation_verified=True,
                )
                return
            if pending and "确认" in normalized:
                send_message(
                    message_id,
                    f"确认码不正确或已过期，请回复“确认 {pending.get('code')}”。",
                )
                return
            planning_text, sensitive_fields = _redact_sensitive_fields(text)
            if sensitive_fields:
                send_message(
                    message_id,
                    "为了保护密钥，本条消息不会交给智能体。请在网页设置中配置密钥。",
                )
                return
            if agent is None:
                legacy.dispatch(text, message_id, scope_id, open_id)
                return
            batch_id = sessions.current_batch_id(scope_id)
            current_batch = None
            if batch_id:
                try:
                    current_batch = service.get_batch(batch_id)
                except KeyError:
                    pass
            plan = agent.plan(
                planning_text,
                accounts=service.list_accounts(),
                current_batch=current_batch,
                recent_hot_topics=sessions.recent_hot_topics(scope_id),
                review_state=sessions.review_state(scope_id),
            )
            if plan.tool != "chat":
                send_message(message_id, plan.plan_text)
            executor.execute(
                plan,
                original_text=text,
                message_id=message_id,
                chat_id=scope_id,
                open_id=open_id,
                current_batch_id=batch_id,
            )
        except Exception as exc:
            safe_error = sanitize_failure_text(
                _redact_known_values(str(exc), locals().get("sensitive_fields", {}))
            )
            try:
                send_message(message_id, f"微信指挥执行失败：{safe_error}")
            except Exception as reply_exc:  # noqa: BLE001
                logger.warning(
                    "Unable to report WeChat command failure: %s",
                    sanitize_failure_text(reply_exc),
                )
            raise

    def _account(self, account_id: str) -> dict[str, Any]:
        account = self.db.get_official_account(str(account_id or "").strip())
        if not account:
            raise ValueError("公众号不存在或不属于当前用户")
        return dict(account)

    def _load(self, account_id: str) -> dict[str, Any]:
        raw = self.db.get_user_setting(SETTING_PREFIX + str(account_id).strip())
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _save(self, account_id: str, value: dict[str, Any]) -> None:
        self.db.set_user_setting(
            SETTING_PREFIX + str(account_id).strip(),
            json.dumps(value, ensure_ascii=False),
        )
