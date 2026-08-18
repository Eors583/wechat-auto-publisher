from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import ipaddress
import threading
import time
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.db import Database
from app.services.local_agents import LocalAgentError, LocalAgentService


class PairStartRequest(BaseModel):
    device_name: str = Field(default="Windows 本机助手", max_length=100)


class PairApproveRequest(BaseModel):
    pairing_id: str = Field(min_length=10, max_length=100)
    user_code: str = Field(min_length=8, max_length=8, pattern=r"^\d{8}$")


class PairTokenRequest(BaseModel):
    device_code: str = Field(min_length=32, max_length=256)


class RenameAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class HeartbeatRequest(BaseModel):
    cockpit_status: str = Field(default="unknown", max_length=40)
    last_error_code: str = Field(default="", max_length=100)


class LeaseRequest(BaseModel):
    attempt_id: str = Field(min_length=16, max_length=100)
    nonce: str = Field(min_length=16, max_length=100)


class ResultRequest(LeaseRequest):
    status: str
    response_text: str = Field(default="", max_length=16 * 1024 * 1024)
    error_code: str = Field(default="", max_length=100)
    error: str = Field(default="", max_length=2000)


class _SlidingWindowLimiter:
    _MAX_KEYS = 4096

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: OrderedDict[str, list[float]] = OrderedDict()
        self._last_cleanup = 0.0

    def allow(self, key: str, *, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        cutoff = now - float(window_seconds)
        with self._lock:
            if now - self._last_cleanup >= 5.0:
                stale_cutoff = now - 60.0
                for stale_key in [
                    existing_key
                    for existing_key, existing_calls in self._calls.items()
                    if not existing_calls or existing_calls[-1] <= stale_cutoff
                ]:
                    self._calls.pop(stale_key, None)
                self._last_cleanup = now
            calls = [item for item in self._calls.get(key, []) if item > cutoff]
            if key not in self._calls and len(self._calls) >= self._MAX_KEYS:
                self._calls.popitem(last=False)
            if len(calls) >= int(limit):
                self._calls[key] = calls
                self._calls.move_to_end(key)
                return False
            calls.append(now)
            self._calls[key] = calls
            self._calls.move_to_end(key)
        return True


def _rate_limit_source(request: Request) -> str:
    """Use the last forwarded hop only when the direct peer is trusted-local."""

    peer = str((request.client or ("unknown", 0))[0]).strip() or "unknown"
    try:
        trusted_proxy = ipaddress.ip_address(peer).is_private or ipaddress.ip_address(
            peer
        ).is_loopback
    except ValueError:
        trusted_proxy = False
    if not trusted_proxy:
        return peer
    forwarded = str(request.headers.get("x-forwarded-for") or "")
    if not forwarded:
        return peer
    candidate = forwarded.rsplit(",", 1)[-1].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer


def create_local_agent_router(
    db: Database,
    require_token: Callable[..., dict[str, Any]],
) -> APIRouter:
    router = APIRouter()
    service = LocalAgentService(db)
    limiter = _SlidingWindowLimiter()

    def raise_agent_error(exc: LocalAgentError) -> None:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    def require_agent(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        prefix = "Bearer "
        raw_token = (
            authorization[len(prefix):].strip()
            if authorization and authorization.startswith(prefix)
            else ""
        )
        try:
            return service.authenticate_agent(raw_token)
        except LocalAgentError as exc:
            raise_agent_error(exc)

    @router.post("/api/v1/local-agents/pair/start")
    def start_pairing(
        payload: PairStartRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        source = _rate_limit_source(request)
        if not limiter.allow(f"start:{source}", limit=6, window_seconds=60):
            raise HTTPException(
                status_code=429,
                detail="配对请求过于频繁，请稍后重试",
                headers={"Retry-After": "60"},
            )
        response.headers["Cache-Control"] = "no-store"
        try:
            return service.start_pairing(payload.device_name)
        except LocalAgentError as exc:
            raise_agent_error(exc)

    @router.post("/api/v1/local-agents/pair/approve")
    def approve_pairing(
        payload: PairApproveRequest,
        principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        try:
            return service.approve_pairing(
                str(principal["id"]),
                payload.pairing_id,
                payload.user_code,
            )
        except LocalAgentError as exc:
            raise_agent_error(exc)

    @router.post("/api/v1/local-agents/pair/token")
    def exchange_pairing(
        payload: PairTokenRequest,
        request: Request,
        response: Response,
    ) -> Any:
        source = _rate_limit_source(request)
        if not limiter.allow(f"token-source:{source}", limit=120, window_seconds=60):
            raise HTTPException(
                status_code=429,
                detail="设备令牌轮询来源请求过于频繁",
                headers={"Retry-After": "3"},
            )
        device_key = hashlib.sha256(payload.device_code.encode("utf-8")).hexdigest()
        if not limiter.allow(f"token:{device_key}", limit=30, window_seconds=60):
            raise HTTPException(
                status_code=429,
                detail="设备令牌轮询过于频繁",
                headers={"Retry-After": "3"},
            )
        response.headers["Cache-Control"] = "no-store"
        try:
            return service.exchange_pairing(payload.device_code)
        except LocalAgentError as exc:
            if exc.status_code == 202:
                return JSONResponse(
                    status_code=202,
                    headers={"Cache-Control": "no-store", "Retry-After": "3"},
                    content={
                        "status": exc.code,
                        "message": str(exc),
                        "retry_after": 3,
                    },
                )
            if exc.status_code == 429:
                return JSONResponse(
                    status_code=429,
                    headers={"Cache-Control": "no-store", "Retry-After": "3"},
                    content={"status": exc.code, "message": str(exc)},
                )
            raise_agent_error(exc)

    @router.get("/api/v1/local-agents")
    def list_agents(
        principal: dict[str, Any] = Depends(require_token),
    ) -> list[dict[str, Any]]:
        return service.list_agents(str(principal["id"]))

    @router.patch("/api/v1/local-agents/{agent_id}")
    def rename_agent(
        agent_id: str,
        payload: RenameAgentRequest,
        principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        try:
            return service.rename_agent(
                str(principal["id"]),
                agent_id,
                payload.name,
            )
        except LocalAgentError as exc:
            raise_agent_error(exc)

    @router.delete("/api/v1/local-agents/{agent_id}")
    def revoke_agent(
        agent_id: str,
        principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        try:
            return service.revoke_agent(str(principal["id"]), agent_id)
        except LocalAgentError as exc:
            raise_agent_error(exc)

    @router.post("/api/v1/local-agents/heartbeat")
    def heartbeat(
        payload: HeartbeatRequest,
        agent: dict[str, Any] = Depends(require_agent),
    ) -> dict[str, Any]:
        try:
            return service.heartbeat(
                agent,
                cockpit_status=payload.cockpit_status,
                last_error_code=payload.last_error_code,
            )
        except LocalAgentError as exc:
            raise_agent_error(exc)

    @router.post("/api/v1/local-agents/jobs/claim")
    async def claim_job(
        wait: int = Query(default=25, ge=0, le=25),
        agent: dict[str, Any] = Depends(require_agent),
    ) -> Any:
        deadline = time.monotonic() + int(wait)
        while True:
            try:
                job = await run_in_threadpool(service.try_claim_job, agent)
            except LocalAgentError as exc:
                raise_agent_error(exc)
            if job is not None:
                return job
            if time.monotonic() >= deadline:
                return Response(status_code=204)
            await asyncio.sleep(0.5)

    @router.post("/api/v1/local-agents/jobs/{request_id}/lease")
    def renew_lease(
        request_id: str,
        payload: LeaseRequest,
        agent: dict[str, Any] = Depends(require_agent),
    ) -> dict[str, Any]:
        try:
            return service.renew_lease(
                agent,
                request_id,
                payload.attempt_id,
                payload.nonce,
            )
        except LocalAgentError as exc:
            raise_agent_error(exc)

    @router.post("/api/v1/local-agents/jobs/{request_id}/result")
    def submit_result(
        request_id: str,
        payload: ResultRequest,
        agent: dict[str, Any] = Depends(require_agent),
    ) -> dict[str, Any]:
        try:
            return service.submit_result(
                agent,
                request_id,
                attempt_id=payload.attempt_id,
                nonce=payload.nonce,
                status=payload.status,
                response_text=payload.response_text,
                error_code=payload.error_code,
                error=payload.error,
            )
        except LocalAgentError as exc:
            raise_agent_error(exc)

    return router


__all__ = ["create_local_agent_router"]
