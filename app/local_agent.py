from __future__ import annotations

import json
import logging
import os
import random
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.local_credentials import LocalCredentialStore, LocalSecureStateStore
from app.local_model_cors_bridge import create_handler


DEFAULT_REMOTE_URL = "https://api.bluebloodlab.cn/publisher/"
COCKPIT_CHAT_URL = "http://127.0.0.1:11797/v1/chat/completions"
COCKPIT_MODELS_URL = "http://127.0.0.1:11797/v1/models"
SETUP_URL = "http://127.0.0.1:11798/setup"
MAX_JOB_BYTES = 16 * 1024 * 1024
TOKEN_REJECTION_CODES = {
    "agent_token_invalid",
    "agent_revoked",
    "agent_token_missing",
}
logger = logging.getLogger(__name__)


def _origin_from_remote_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("本机 Companion 必须连接生产 HTTPS 地址")
    return f"{parsed.scheme}://{parsed.netloc}"


def _show_startup_error(message: str) -> None:
    if os.name != "nt":  # pragma: no cover - Windows is the product target
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(
        None,
        str(message),
        "BlueBloodLab 本机助手",
        0x10,
    )


def _application_rejected_agent_token(response: httpx.Response) -> bool:
    if response.status_code not in {401, 403}:
        return False
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return False
    detail = payload.get("detail") if isinstance(payload, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else ""
    return str(code or "").strip().casefold() in TOKEN_REJECTION_CODES


class SingleInstanceLock:
    """Hold one per-user file lock for the lifetime of the companion."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: Any | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - Windows is the supported product target
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> SingleInstanceLock:
        if not self.acquire():
            raise RuntimeError("本机 Companion 已经在运行")
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class LocalAgent:
    """Outbound-only Windows companion for fixed Cockpit chat jobs."""

    def __init__(
        self,
        remote_url: str = DEFAULT_REMOTE_URL,
        *,
        state_store: LocalSecureStateStore | None = None,
        credential_store: LocalCredentialStore | None = None,
    ) -> None:
        self.remote_url = str(remote_url or DEFAULT_REMOTE_URL).rstrip("/") + "/"
        self.api_origin = _origin_from_remote_url(self.remote_url)
        self.state_store = state_store or LocalSecureStateStore()
        self.credential_store = credential_store or LocalCredentialStore()
        self.stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._connection_status = "未连接"
        self._last_error_code = ""
        try:
            self.state = self.state_store.load()
        except RuntimeError:
            self.state = {}
            self._last_error_code = "agent.state_corrupt"

    def _save_state(self) -> None:
        with self._state_lock:
            self.state_store.save(self.state)

    def public_status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "paired": bool(self.state.get("agent_token")),
                "agent_id": str(self.state.get("agent_id") or ""),
                "pairing_id": str(self.state.get("pairing_id") or ""),
                "user_code": str(self.state.get("user_code") or ""),
                "verification_uri_complete": str(
                    self.state.get("verification_uri_complete") or ""
                ),
                "connection_status": self._connection_status,
                "last_error_code": self._last_error_code,
                "autostart_enabled": self.autostart_enabled(),
            }

    @staticmethod
    def autostart_enabled() -> bool:
        if os.name != "nt":
            return False
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as key:
                winreg.QueryValueEx(key, "BlueBloodLabCockpitBridge")
            return True
        except OSError:
            return False

    def set_autostart(self, enabled: bool) -> None:
        if os.name != "nt":
            raise RuntimeError("登录自启动目前只支持 Windows")
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            if enabled:
                command = self._autostart_command()
                winreg.SetValueEx(
                    key,
                    "BlueBloodLabCockpitBridge",
                    0,
                    winreg.REG_SZ,
                    command,
                )
            else:
                try:
                    winreg.DeleteValue(key, "BlueBloodLabCockpitBridge")
                except FileNotFoundError:
                    pass

    def _autostart_command(self) -> str:
        module_entry = "" if getattr(sys, "frozen", False) else " -m app.launcher"
        return (
            f'"{sys.executable}"{module_entry} --local-agent '
            f'--remote-url "{self.remote_url}"'
        )

    def request_stop(self) -> None:
        threading.Timer(0.25, self.stop).start()

    def start_pairing(self) -> dict[str, Any]:
        with self._state_lock:
            if self.state.get("agent_token"):
                raise RuntimeError(
                    "本机助手已配对；请先在生产工作台撤销旧设备"
                )
        response = httpx.post(
            f"{self.api_origin}/api/v1/local-agents/pair/start",
            json={"device_name": socket.gethostname() or "Windows 本机助手"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        required = {
            "pairing_id",
            "device_code",
            "user_code",
            "verification_uri_complete",
        }
        if not isinstance(payload, dict) or not required.issubset(payload):
            raise RuntimeError("生产服务返回了无效的配对响应")
        with self._state_lock:
            self.state.update(
                {
                    "pairing_id": str(payload["pairing_id"]),
                    "device_code": str(payload["device_code"]),
                    "user_code": str(payload["user_code"]),
                    "verification_uri_complete": str(
                        payload["verification_uri_complete"]
                    ),
                    "pairing_started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._save_state()
        return self.public_status()

    def _exchange_pairing(self) -> bool:
        with self._state_lock:
            if self.state.get("agent_token"):
                changed = False
                for key in (
                    "pairing_id",
                    "device_code",
                    "user_code",
                    "verification_uri_complete",
                ):
                    changed = self.state.pop(key, None) is not None or changed
                if changed:
                    self._save_state()
                return False
            device_code = str(self.state.get("device_code") or "")
        if not device_code:
            return False
        response = httpx.post(
            f"{self.api_origin}/api/v1/local-agents/pair/token",
            json={"device_code": device_code},
            timeout=15,
        )
        if response.status_code == 202:
            self._connection_status = "等待网页批准"
            return False
        if response.status_code in {409, 410, 423}:
            with self._state_lock:
                for key in (
                    "pairing_id",
                    "device_code",
                    "user_code",
                    "verification_uri_complete",
                ):
                    self.state.pop(key, None)
                self._save_state()
            self._last_error_code = "agent.pairing_expired"
            return False
        response.raise_for_status()
        payload = response.json()
        agent_id = str(payload.get("agent_id") or "")
        agent_token = str(payload.get("agent_token") or "")
        if not agent_id or len(agent_token) < 32:
            raise RuntimeError("生产服务返回了无效的 Agent Token")
        with self._state_lock:
            self.state["agent_id"] = agent_id
            self.state["agent_token"] = agent_token
            for key in (
                "pairing_id",
                "device_code",
                "user_code",
                "verification_uri_complete",
            ):
                self.state.pop(key, None)
            self._save_state()
        self._connection_status = "已配对"
        return True

    def _agent_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {str(self.state.get('agent_token') or '')}",
            "Accept": "application/json",
        }

    def _revoke_local_token(self) -> None:
        with self._state_lock:
            for key in ("agent_id", "agent_token", "pending_result"):
                self.state.pop(key, None)
            self._save_state()
        self._connection_status = "设备已撤销，等待重新配对"
        self._last_error_code = "agent.revoked"

    def _cockpit_status(self) -> tuple[str, str]:
        key = self.credential_store.load_api_key()
        if not key:
            return "unauthorized", "cockpit.key_missing"
        try:
            with httpx.Client(trust_env=False, timeout=5) as client:
                response = client.get(
                    COCKPIT_MODELS_URL,
                    headers={"Authorization": f"Bearer {key}"},
                )
            if response.status_code in {401, 403}:
                return "unauthorized", "cockpit.unauthorized"
            if response.status_code >= 400:
                return "unavailable", f"cockpit.http_{response.status_code}"
            return "ready", ""
        except (httpx.HTTPError, OSError):
            return "unavailable", "cockpit.unavailable"

    def _heartbeat(self) -> bool:
        cockpit_status, error_code = self._cockpit_status()
        response = httpx.post(
            f"{self.api_origin}/api/v1/local-agents/heartbeat",
            headers=self._agent_headers(),
            json={
                "cockpit_status": cockpit_status,
                "last_error_code": error_code,
            },
            timeout=15,
        )
        if _application_rejected_agent_token(response):
            self._revoke_local_token()
            return False
        response.raise_for_status()
        return True

    @staticmethod
    def _validate_job(job: Any) -> dict[str, Any]:
        if not isinstance(job, dict):
            raise RuntimeError("本机任务不是 JSON 对象")
        if str(job.get("operation") or "") != "chat.completions":
            raise RuntimeError("生产服务下发了不允许的本机操作")
        forbidden = {"url", "headers", "method", "authorization", "api_base"}
        if forbidden.intersection(str(key).casefold() for key in job):
            raise RuntimeError("生产服务下发了不允许的网络参数")
        required = {"request_id", "attempt_id", "nonce", "deadline_at", "payload"}
        if not required.issubset(job):
            raise RuntimeError("本机任务字段不完整")
        payload = job.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("本机模型载荷无效")
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_JOB_BYTES:
            raise RuntimeError("本机模型载荷过大")
        if not str(payload.get("model") or "").strip() or not isinstance(
            payload.get("messages"), list
        ):
            raise RuntimeError("本机模型载荷缺少模型或消息")
        try:
            deadline = datetime.fromisoformat(str(job["deadline_at"]))
            if deadline.tzinfo is None or deadline.utcoffset() is None:
                raise ValueError("timezone required")
            deadline = deadline.astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("本机任务截止时间无效") from exc
        if deadline <= datetime.now(timezone.utc):
            raise RuntimeError("本机任务已经过期")
        return dict(job)

    def _renew_lease_loop(
        self,
        job: dict[str, Any],
        finished: threading.Event,
        lease_lost: threading.Event,
    ) -> None:
        while not finished.wait(20):
            try:
                response = httpx.post(
                    f"{self.api_origin}/api/v1/local-agents/jobs/"
                    f"{job['request_id']}/lease",
                    headers=self._agent_headers(),
                    json={
                        "attempt_id": job["attempt_id"],
                        "nonce": job["nonce"],
                    },
                    timeout=15,
                )
                if response.status_code != 200:
                    lease_lost.set()
                    return
            except httpx.HTTPError:
                # A transient renewal failure gets one more 20-second window;
                # the server's exact lease predicate still rejects stale work.
                continue

    def _run_cockpit_job(self, job: dict[str, Any]) -> dict[str, Any]:
        key = self.credential_store.load_api_key()
        if not key:
            return {
                "status": "failed",
                "response_text": "",
                "error_code": "cockpit.key_missing",
                "error": "本机助手尚未配置 Cockpit API Key",
            }
        finished = threading.Event()
        lease_lost = threading.Event()
        renewer = threading.Thread(
            target=self._renew_lease_loop,
            args=(job, finished, lease_lost),
            name="local-agent-lease",
            daemon=True,
        )
        renewer.start()
        try:
            with httpx.Client(
                trust_env=False,
                timeout=httpx.Timeout(620, connect=5),
            ) as client:
                with client.stream(
                    "POST",
                    COCKPIT_CHAT_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=job["payload"],
                ) as response:
                    if response.status_code in {401, 403}:
                        raise RuntimeError("Cockpit API Key 无效")
                    if response.status_code == 404:
                        raise RuntimeError("Cockpit 模型名称或接口不存在")
                    if response.status_code == 429:
                        raise RuntimeError("Cockpit 当前限流")
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_JOB_BYTES:
                            raise RuntimeError("Cockpit 返回结果超过 16 MiB 限制")
                        chunks.append(chunk)
                    response_body = b"".join(chunks)
                    if key.encode("utf-8") in response_body:
                        raise RuntimeError(
                            "Cockpit 响应包含受保护凭据，已在本机阻止上传"
                        )
            if lease_lost.is_set():
                raise RuntimeError("任务租约已丢失")
            data = json.loads(response_body)
            if not isinstance(data, dict):
                raise RuntimeError("Cockpit 返回 JSON 结构无效")
            choices = data.get("choices")
            if (
                not isinstance(choices, list)
                or not choices
                or not isinstance(choices[0], dict)
            ):
                raise RuntimeError("Cockpit 返回 JSON 结构无效")
            message = choices[0].get("message")
            if not isinstance(message, dict) or not isinstance(
                message.get("content"),
                str,
            ):
                raise RuntimeError("Cockpit 返回 JSON 结构无效")
            content = str(message["content"]).strip()
            if not content:
                raise RuntimeError("Cockpit 返回内容为空")
            return {
                "status": "completed",
                "response_text": content,
                "error_code": "",
                "error": "",
            }
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            message = str(exc)
            if "租约" in message:
                code = "agent.lease_lost"
            elif "受保护凭据" in message:
                code = "cockpit.credential_echo"
            elif "Key" in message:
                code = "cockpit.unauthorized"
            elif "模型名称" in message:
                code = "cockpit.model_not_found"
            elif "限流" in message:
                code = "cockpit.rate_limited"
            else:
                code = "cockpit.request_failed"
            return {
                "status": "failed",
                "response_text": "",
                "error_code": code,
                "error": message[:500],
            }
        finally:
            finished.set()
            renewer.join(timeout=1)

    def _submit_pending_result(self) -> bool:
        pending = self.state.get("pending_result")
        if not isinstance(pending, dict):
            return True
        request_id = str(pending.get("request_id") or "")
        response = httpx.post(
            f"{self.api_origin}/api/v1/local-agents/jobs/{request_id}/result",
            headers=self._agent_headers(),
            json={key: value for key, value in pending.items() if key != "request_id"},
            timeout=30,
        )
        if _application_rejected_agent_token(response):
            self._revoke_local_token()
            return False
        if response.status_code == 409 or response.status_code == 200:
            with self._state_lock:
                self.state.pop("pending_result", None)
                self._save_state()
            return True
        response.raise_for_status()
        return False

    def _claim_once(self) -> bool:
        response = httpx.post(
            f"{self.api_origin}/api/v1/local-agents/jobs/claim",
            params={"wait": 25},
            headers=self._agent_headers(),
            timeout=40,
        )
        if response.status_code == 204:
            return False
        if _application_rejected_agent_token(response):
            self._revoke_local_token()
            return False
        response.raise_for_status()
        job = self._validate_job(response.json())
        result = self._run_cockpit_job(job)
        pending = {
            "request_id": str(job["request_id"]),
            "attempt_id": str(job["attempt_id"]),
            "nonce": str(job["nonce"]),
            **result,
        }
        with self._state_lock:
            self.state["pending_result"] = pending
            self._save_state()
        self._submit_pending_result()
        return True

    def _start_bridge(self) -> None:
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", 11798),
            create_handler(
                credential_store=self.credential_store,
                agent_controller=self,
            ),
        )
        threading.Thread(
            target=self._server.serve_forever,
            name="cockpit-loopback-bridge",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self.stop_event.set()
        if self._server is not None:
            self._server.shutdown()

    def run(self, *, open_setup: bool = False) -> int:
        try:
            self._start_bridge()
        except OSError:
            self._connection_status = "11798 端口被旧本机助手占用"
            self._last_error_code = "agent.bridge_port_in_use"
            logger.error(
                "Local agent cannot bind 127.0.0.1:11798; stop the old bridge first"
            )
            if open_setup:
                _show_startup_error(
                    "无法启动本机助手：127.0.0.1:11798 已被旧桥或其他程序占用。"
                    "请先退出旧桥；如果无法确认占用者，请重启 Windows 后再打开本机助手。"
                )
            return 2
        if open_setup:
            webbrowser.open(SETUP_URL)
        failures = 0
        last_heartbeat = 0.0
        try:
            while not self.stop_event.is_set():
                try:
                    if self.state.get("device_code"):
                        self._exchange_pairing()
                        self.stop_event.wait(3)
                        continue
                    if not self.state.get("agent_token"):
                        self._connection_status = "等待配对"
                        self.stop_event.wait(2)
                        continue
                    if self.state.get("pending_result"):
                        self._submit_pending_result()
                    if time.monotonic() - last_heartbeat >= 20:
                        self._heartbeat()
                        last_heartbeat = time.monotonic()
                    self._connection_status = "生产服务已连接"
                    self._claim_once()
                    failures = 0
                except (
                    httpx.HTTPError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ) as exc:
                    failures += 1
                    self._connection_status = "生产连接异常"
                    self._last_error_code = "agent.connection_failed"
                    logger.warning(
                        "Local agent iteration failed: %s",
                        type(exc).__name__,
                    )
                    delay = min(30.0, float(2 ** min(failures, 5)))
                    self.stop_event.wait(delay * random.uniform(0.8, 1.2))
        finally:
            self.stop()
        return 0


def run_local_agent(
    remote_url: str = DEFAULT_REMOTE_URL,
    *,
    open_setup: bool = False,
) -> int:
    state_store = LocalSecureStateStore()
    lock = SingleInstanceLock(state_store.directory / "agent.lock")
    if not lock.acquire():
        if open_setup:
            webbrowser.open(SETUP_URL)
        return 0
    try:
        return LocalAgent(
            remote_url,
            state_store=state_store,
        ).run(open_setup=open_setup)
    finally:
        lock.release()


def local_agent_self_test(directory: str | Path) -> dict[str, Any]:
    target = Path(directory)
    store = LocalSecureStateStore(target)
    sample = {"agent_token": "self-test-token", "pending_result": {"status": "ok"}}
    store.save(sample)
    restored = store.load()
    store.clear()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        loopback_port = int(listener.getsockname()[1])
    return {
        "ok": restored == sample,
        "loopback_bind": f"127.0.0.1:{loopback_port}",
        "remote_origin": _origin_from_remote_url(DEFAULT_REMOTE_URL),
    }


__all__ = [
    "COCKPIT_CHAT_URL",
    "DEFAULT_REMOTE_URL",
    "LocalAgent",
    "SingleInstanceLock",
    "local_agent_self_test",
    "run_local_agent",
]
