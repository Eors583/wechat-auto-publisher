from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol


logger = logging.getLogger(__name__)


class ApiProcessControlError(RuntimeError):
    """Raised when the current desktop window cannot safely control its API."""


class ManagedProcess(Protocol):
    pid: int | None

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


ProcessFactory = Callable[..., ManagedProcess]
HealthCheck = Callable[[int], bool]
PortCheck = Callable[[int], bool]
WaitForHealth = Callable[[int, float, str], bool]


class OwnedApiProcessController:
    """Own and restart only the API process created by this desktop window.

    The controller intentionally never discovers a process by PID or by the
    listening port.  A process is controllable only when its ``Process`` handle
    was created and retained by this controller.
    """

    def __init__(
        self,
        *,
        port: int,
        target: Callable[[], None],
        session_id: str,
        health_check: HealthCheck,
        port_check: PortCheck,
        wait_for_health: WaitForHealth,
        process_factory: ProcessFactory = multiprocessing.Process,
        before_restart: Callable[[], None] | None = None,
        startup_timeout: float = 20.0,
        stop_timeout: float = 5.0,
    ) -> None:
        self.port = int(port)
        self.session_id = str(session_id or "").strip()
        self._target = target
        self._health_check = health_check
        self._port_check = port_check
        self._wait_for_health = wait_for_health
        self._process_factory = process_factory
        self._before_restart = before_restart
        self._startup_timeout = max(1.0, float(startup_timeout))
        self._stop_timeout = max(0.2, float(stop_timeout))
        self._lock = threading.RLock()
        self._restart_gate = threading.Lock()
        self._process: ManagedProcess | None = None
        self._closed = False
        self._unowned_reason = ""

    @property
    def owns_process(self) -> bool:
        with self._lock:
            return self._process is not None and not self._closed

    @property
    def pid(self) -> int | None:
        with self._lock:
            return (
                int(self._process.pid)
                if self._process is not None and self._process.pid is not None
                else None
            )

    @property
    def unowned_reason(self) -> str:
        with self._lock:
            return self._unowned_reason

    def ensure_started(self) -> bool:
        """Start the API when the configured port is available.

        ``False`` means a compatible API was already running but is not owned
        by this window.  A foreign listener or a failed owned startup raises a
        user-facing error.
        """

        with self._lock:
            self._ensure_open()
            if self._process is not None and self._process.is_alive():
                return True
            if self._health_check(self.port):
                self._unowned_reason = (
                    "API/飞书服务已经由另一个应用窗口启动；"
                    "当前窗口没有持有该服务进程，不能替它执行重启。"
                )
                return False
            if self._port_check(self.port):
                self._unowned_reason = (
                    f"本机端口 {self.port} 已被当前窗口之外的程序占用。"
                    "为避免误关其他程序，未启动也不会重启该进程。"
                )
                raise ApiProcessControlError(self._unowned_reason)
            self._spawn_locked()
            return True

    def restart(self) -> dict[str, Any]:
        """Replace the API child process owned by this controller."""

        if not self._restart_gate.acquire(blocking=False):
            raise ApiProcessControlError(
                "API/飞书服务正在重启，请等待当前操作完成后再试。"
            )
        try:
            with self._lock:
                self._ensure_open()
                if self._process is None:
                    reason = self._unowned_reason or (
                        "当前窗口没有启动并持有 API/飞书服务进程，"
                        "不能安全重启。请关闭其他应用窗口后，从统一启动入口重新打开。"
                    )
                    raise ApiProcessControlError(reason)

                old_pid = self.pid
                if self._before_restart is not None:
                    try:
                        self._before_restart()
                    except Exception:  # telemetry must not block a safe restart
                        logger.exception(
                            "Unable to mark Feishu runtime as restarting"
                        )

                self._stop_owned_locked()
                if not self._wait_for_port_release_locked():
                    raise ApiProcessControlError(
                        f"当前窗口启动的 API 进程已经停止，但端口 {self.port} "
                        "仍被占用。为避免误杀其他程序，本次没有继续启动新服务。"
                    )

                self._spawn_locked()
                new_pid = self.pid
                return {
                    "ok": True,
                    "old_pid": old_pid,
                    "new_pid": new_pid,
                    "port": self.port,
                    "message": "API 与飞书服务已重启，新配置已经重新加载。",
                }
        finally:
            self._restart_gate.release()

    def shutdown(self) -> None:
        """Stop the owned child while leaving every unowned process untouched."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop_owned_locked()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ApiProcessControlError(
                "当前应用窗口正在关闭，不能再重启 API/飞书服务。"
            )

    def _spawn_locked(self) -> None:
        try:
            process = self._process_factory(
                target=self._target,
                name="wechat-publisher-api",
                daemon=True,
            )
            self._process = process
            self._unowned_reason = ""
            process.start()
            started = self._wait_for_health(
                self.port,
                self._startup_timeout,
                self.session_id,
            )
        except ApiProcessControlError:
            raise
        except Exception as exc:
            logger.exception("Unable to start the owned API process")
            raise ApiProcessControlError(
                "当前窗口无法启动 API/飞书服务进程。"
                "请查看 data/logs/launcher.log 获取具体原因。"
            ) from exc
        if started:
            return

        self._stop_owned_locked()
        raise ApiProcessControlError(
            "API/飞书服务没有在预期时间内启动。"
            "请查看 data/logs/api.log 获取具体原因。"
        )

    def _stop_owned_locked(self) -> None:
        process = self._process
        if process is None or not process.is_alive():
            return
        process.terminate()
        process.join(timeout=self._stop_timeout)
        if process.is_alive():
            # ``kill`` is safe here because this is still the exact Process
            # object created by this controller, never a PID found by scanning.
            process.kill()
            process.join(timeout=self._stop_timeout)
        if process.is_alive():
            raise ApiProcessControlError(
                "当前窗口启动的 API/飞书服务未能停止，已取消重启。"
            )

    def _wait_for_port_release_locked(self) -> bool:
        deadline = time.monotonic() + self._stop_timeout
        while time.monotonic() < deadline:
            if not self._port_check(self.port):
                return True
            time.sleep(0.05)
        return not self._port_check(self.port)


_controller_lock = threading.RLock()
_api_controller: OwnedApiProcessController | None = None


def register_api_process_controller(
    controller: OwnedApiProcessController,
) -> None:
    """Register the controller owned by the current launcher process."""

    global _api_controller
    with _controller_lock:
        _api_controller = controller


def clear_api_process_controller(
    controller: OwnedApiProcessController | None = None,
) -> None:
    """Clear a registration without shutting down an unrelated controller."""

    global _api_controller
    with _controller_lock:
        if controller is None or _api_controller is controller:
            _api_controller = None


def api_service_restart_available() -> bool:
    with _controller_lock:
        controller = _api_controller
    return bool(controller is not None and controller.owns_process)


def restart_api_service() -> dict[str, Any]:
    """Restart the API/Feishu child owned by the current desktop window."""

    with _controller_lock:
        controller = _api_controller
    if controller is None:
        raise ApiProcessControlError(
            "当前启动方式没有 API/飞书服务控制器，无法在页面内安全重启。"
            "请通过“启动改写助手”或 python -m app.launcher 打开应用。"
        )
    return controller.restart()


__all__ = [
    "ApiProcessControlError",
    "OwnedApiProcessController",
    "api_service_restart_available",
    "clear_api_process_controller",
    "register_api_process_controller",
    "restart_api_service",
]
