from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from app.db import Database
from app.services.auth import token_hash
from app.services.failures import sanitize_failure_text


PAIRING_TTL_SECONDS = 600
PAIRING_POLL_SECONDS = 3
PAIRING_HASH_ITERATIONS = 120_000
LEASE_SECONDS = 60
_USER_CODE_PATTERN = re.compile(r"^\d{8}$")
_ERROR_CODE_PATTERN = re.compile(r"^[a-z0-9_.-]{0,100}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _user_code_hash(code: str, salt_hex: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        str(code).encode("ascii"),
        bytes.fromhex(str(salt_hex)),
        int(iterations),
    ).hex()


class LocalAgentError(ValueError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code)


class LocalAgentService:
    """Pair, authenticate and lease fixed local-model work to companions."""

    def __init__(
        self,
        db: Database,
        *,
        production_origin: str = "https://api.bluebloodlab.cn",
    ) -> None:
        self.db = db
        self.production_origin = production_origin.rstrip("/")

    def start_pairing(self, device_name: str) -> dict[str, Any]:
        name = str(device_name or "").strip()[:100] or "Windows 本机助手"
        pairing_id = f"pair_{uuid.uuid4().hex}"
        device_code = secrets.token_urlsafe(40)
        user_code = f"{secrets.randbelow(100_000_000):08d}"
        salt = os.urandom(16).hex()
        expires_at = _utc_now() + timedelta(seconds=PAIRING_TTL_SECONDS)
        created = self.db.create_local_agent_pairing(
            {
                "id": pairing_id,
                "device_code_hash": token_hash(device_code),
                "user_code_salt": salt,
                "user_code_hash": _user_code_hash(
                    user_code,
                    salt,
                    PAIRING_HASH_ITERATIONS,
                ),
                "hash_iterations": PAIRING_HASH_ITERATIONS,
                "device_name": name,
                "expires_at": _iso(expires_at),
            }
        )
        if not created:
            raise LocalAgentError(
                429,
                "pairing_capacity_reached",
                "待处理配对请求过多，请稍后重试",
            )
        query = urlencode({"pairing_id": pairing_id})
        verification_uri = f"{self.production_origin}/publisher/"
        return {
            "pairing_id": pairing_id,
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": f"{verification_uri}?{query}",
            "expires_in": PAIRING_TTL_SECONDS,
            "poll_interval": PAIRING_POLL_SECONDS,
        }

    def approve_pairing(
        self,
        owner_user_id: str,
        pairing_id: str,
        user_code: str,
    ) -> dict[str, Any]:
        clean_owner = str(owner_user_id or "").strip()
        clean_code = str(user_code or "").strip()
        if not clean_owner:
            raise LocalAgentError(401, "login_required", "请先登录")
        row = self.db.get_local_agent_pairing(str(pairing_id or "").strip())
        if not row:
            raise LocalAgentError(404, "pairing_not_found", "配对请求不存在")
        status = str(row.get("status") or "")
        if status == "locked":
            raise LocalAgentError(423, "pairing_locked", "配对码错误次数过多，已锁定")
        if status == "consumed":
            raise LocalAgentError(409, "pairing_consumed", "配对码已经使用")
        if status != "pending":
            raise LocalAgentError(409, "pairing_not_pending", "配对请求当前不可批准")
        if str(row.get("expires_at") or "") <= _iso(_utc_now()):
            raise LocalAgentError(410, "pairing_expired", "配对码已过期")
        valid_format = _USER_CODE_PATTERN.fullmatch(clean_code) is not None
        expected = _user_code_hash(
            clean_code if valid_format else "00000000",
            str(row.get("user_code_salt") or ""),
            int(row.get("hash_iterations") or PAIRING_HASH_ITERATIONS),
        )
        if not valid_format or not hmac.compare_digest(
            expected,
            str(row.get("user_code_hash") or ""),
        ):
            failed = self.db.record_local_agent_pairing_failure(str(row["id"]))
            if failed and str(failed.get("status") or "") == "locked":
                raise LocalAgentError(423, "pairing_locked", "配对码错误次数过多，已锁定")
            remaining = max(0, 5 - int((failed or {}).get("failed_attempts") or 0))
            raise LocalAgentError(
                400,
                "pairing_code_invalid",
                f"配对码不正确，还可尝试 {remaining} 次",
            )
        approved = self.db.for_user(clean_owner).approve_local_agent_pairing(
            str(row["id"])
        )
        if not approved:
            raise LocalAgentError(409, "pairing_conflict", "配对状态已变化，请重新开始")
        return {
            "ok": True,
            "pairing_id": str(approved["id"]),
            "device_name": str(approved.get("device_name") or "Windows 本机助手"),
        }

    def exchange_pairing(self, device_code: str) -> dict[str, Any]:
        clean_code = str(device_code or "").strip()
        if len(clean_code) < 32:
            raise LocalAgentError(401, "device_code_invalid", "设备码无效")
        agent_id = f"agent_{uuid.uuid4().hex}"
        agent_token = secrets.token_urlsafe(48)
        result = self.db.exchange_local_agent_pairing(
            device_code_hash=token_hash(clean_code),
            agent_id=agent_id,
            token_hash=token_hash(agent_token),
        )
        state = str(result.get("state") or "invalid")
        agent = result.get("agent")
        if state == "pending":
            raise LocalAgentError(202, "authorization_pending", "等待用户批准")
        if state == "rate_limited":
            raise LocalAgentError(429, "poll_too_fast", "轮询过快，请按指定间隔重试")
        if state == "expired":
            raise LocalAgentError(410, "pairing_expired", "配对码已过期")
        if state == "locked":
            raise LocalAgentError(423, "pairing_locked", "配对码已锁定")
        if state == "consumed" and not agent:
            raise LocalAgentError(409, "pairing_consumed", "配对码已经使用")
        if state != "consumed" or not isinstance(agent, dict):
            raise LocalAgentError(401, "device_code_invalid", "设备码无效")
        return {
            "agent_id": str(agent["id"]),
            "agent_token": agent_token,
            "token_type": "bearer",
        }

    def authenticate_agent(self, raw_token: str) -> dict[str, Any]:
        clean_token = str(raw_token or "").strip()
        if not clean_token:
            raise LocalAgentError(401, "agent_token_missing", "缺少 Agent Token")
        agent = self.db.find_local_model_agent_by_token_hash(
            token_hash(clean_token)
        )
        if not agent:
            raise LocalAgentError(401, "agent_token_invalid", "Agent Token 无效或设备已撤销")
        return agent

    @staticmethod
    def public_agent(agent: dict[str, Any]) -> dict[str, Any]:
        last_seen = str(agent.get("last_seen_at") or "")
        online = False
        if last_seen:
            try:
                online = (
                    _utc_now() - datetime.fromisoformat(last_seen)
                ).total_seconds() <= 60
            except ValueError:
                online = False
        return {
            "id": str(agent.get("id") or ""),
            "name": str(agent.get("name") or "Windows 本机助手"),
            "online": online,
            "last_seen_at": last_seen or None,
            "cockpit_status": str(agent.get("cockpit_status") or "unknown"),
            "last_error_code": str(agent.get("last_error_code") or ""),
            "revoked": bool(str(agent.get("revoked_at") or "").strip()),
            "created_at": agent.get("created_at"),
        }

    def list_agents(self, owner_user_id: str) -> list[dict[str, Any]]:
        return [
            self.public_agent(item)
            for item in self.db.for_user(owner_user_id).list_local_model_agents()
        ]

    def rename_agent(
        self,
        owner_user_id: str,
        agent_id: str,
        name: str,
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name or len(clean_name) > 100:
            raise LocalAgentError(400, "agent_name_invalid", "设备名称不能为空且不能超过 100 字")
        agent = self.db.for_user(owner_user_id).rename_local_model_agent(
            agent_id,
            clean_name,
        )
        if not agent:
            raise LocalAgentError(404, "agent_not_found", "本机设备不存在或已撤销")
        return self.public_agent(agent)

    def revoke_agent(self, owner_user_id: str, agent_id: str) -> dict[str, Any]:
        revoked = self.db.for_user(owner_user_id).revoke_local_model_agent(agent_id)
        if not revoked:
            raise LocalAgentError(404, "agent_not_found", "本机设备不存在或已撤销")
        return {"id": str(agent_id), "revoked": True}

    def heartbeat(
        self,
        agent: dict[str, Any],
        *,
        cockpit_status: str,
        last_error_code: str,
    ) -> dict[str, Any]:
        status = str(cockpit_status or "unknown").strip().casefold()
        if status not in {"ready", "unavailable", "unauthorized", "unknown"}:
            status = "unknown"
        error_code = str(last_error_code or "").strip().casefold()
        if not _ERROR_CODE_PATTERN.fullmatch(error_code):
            error_code = "agent.invalid_error_code"
        scoped = self.db.for_user(str(agent["owner_user_id"]))
        if not scoped.heartbeat_local_model_agent(
            str(agent["id"]),
            cockpit_status=status,
            last_error_code=error_code,
        ):
            raise LocalAgentError(401, "agent_revoked", "本机设备已撤销")
        if status == "ready":
            scoped.clear_local_model_credentials_for_agent(str(agent["id"]))
        return {"ok": True, "lease_seconds": LEASE_SECONDS}

    def claim_job(
        self,
        agent: dict[str, Any],
        *,
        wait_seconds: int = 25,
    ) -> dict[str, Any] | None:
        scoped = self.db.for_user(str(agent["owner_user_id"]))
        deadline = time.monotonic() + max(0, min(int(wait_seconds), 25))
        while True:
            job = scoped.claim_local_agent_request(
                str(agent["id"]),
                lease_seconds=LEASE_SECONDS,
            )
            if job:
                if (
                    job.get("operation") != "chat.completions"
                    or not isinstance(job.get("payload"), dict)
                ):
                    raise LocalAgentError(500, "job_contract_invalid", "本机任务协议无效")
                return job
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.5)

    def try_claim_job(self, agent: dict[str, Any]) -> dict[str, Any] | None:
        job = self.db.for_user(
            str(agent["owner_user_id"])
        ).claim_local_agent_request(
            str(agent["id"]),
            lease_seconds=LEASE_SECONDS,
        )
        if job and (
            job.get("operation") != "chat.completions"
            or not isinstance(job.get("payload"), dict)
        ):
            raise LocalAgentError(500, "job_contract_invalid", "本机任务协议无效")
        return job

    def renew_lease(
        self,
        agent: dict[str, Any],
        request_id: str,
        attempt_id: str,
        nonce: str,
    ) -> dict[str, Any]:
        ok = self.db.for_user(str(agent["owner_user_id"])).renew_local_agent_request(
            request_id,
            str(agent["id"]),
            attempt_id,
            nonce,
            lease_seconds=LEASE_SECONDS,
        )
        if not ok:
            raise LocalAgentError(409, "lease_stale", "任务租约已过期或已被重新分配")
        return {"ok": True, "lease_seconds": LEASE_SECONDS}

    def submit_result(
        self,
        agent: dict[str, Any],
        request_id: str,
        *,
        attempt_id: str,
        nonce: str,
        status: str,
        response_text: str,
        error_code: str,
        error: str,
    ) -> dict[str, Any]:
        clean_status = str(status or "").strip().casefold()
        if clean_status not in {"completed", "failed"}:
            raise LocalAgentError(400, "result_status_invalid", "任务结果状态无效")
        safe_error = sanitize_failure_text(error)[:2000]
        safe_code = str(error_code or "").strip().casefold()
        if not _ERROR_CODE_PATTERN.fullmatch(safe_code):
            safe_code = "agent.invalid_error_code"
        if clean_status == "failed" and not safe_error:
            safe_error = safe_code or "本机模型调用失败"
        clean_response = str(response_text or "") if clean_status == "completed" else ""
        if clean_status == "completed" and not clean_response.strip():
            raise LocalAgentError(400, "result_empty", "本机模型返回了空内容")
        if len(clean_response.encode("utf-8")) > 16 * 1024 * 1024:
            raise LocalAgentError(413, "result_too_large", "本机模型结果超过 16 MiB 限制")
        outcome = self.db.for_user(
            str(agent["owner_user_id"])
        ).complete_local_agent_request(
            request_id,
            str(agent["id"]),
            attempt_id,
            nonce,
            status=clean_status,
            response_text=clean_response,
            error=(safe_error if clean_status == "failed" else ""),
            error_code=(safe_code if clean_status == "failed" else ""),
        )
        if outcome in {"stale", "missing"}:
            raise LocalAgentError(409, "result_stale", "任务已过期或已重新分配")
        return {"ok": True, "result": outcome}


__all__ = [
    "LEASE_SECONDS",
    "LocalAgentError",
    "LocalAgentService",
    "PAIRING_POLL_SECONDS",
    "PAIRING_TTL_SECONDS",
]
