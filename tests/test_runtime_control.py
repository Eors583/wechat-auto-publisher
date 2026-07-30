from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.runtime_control import (
    ApiProcessControlError,
    OwnedApiProcessController,
    api_service_restart_available,
    clear_api_process_controller,
    register_api_process_controller,
    restart_api_service,
)


class _FakeProcess:
    def __init__(
        self,
        *,
        pid: int,
        target: Callable[[], None],
        name: str,
        daemon: bool,
        ignore_terminate: bool = False,
    ) -> None:
        self.pid = pid
        self.target = target
        self.name = name
        self.daemon = daemon
        self.ignore_terminate = ignore_terminate
        self.started = False
        self.alive = False
        self.terminate_calls = 0
        self.kill_calls = 0
        self.join_calls: list[float | None] = []

    def start(self) -> None:
        self.started = True
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminate_calls += 1
        if not self.ignore_terminate:
            self.alive = False

    def kill(self) -> None:
        self.kill_calls += 1
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)


class _ProcessFactory:
    def __init__(self, *, ignore_first_terminate: bool = False) -> None:
        self.processes: list[_FakeProcess] = []
        self.ignore_first_terminate = ignore_first_terminate

    def __call__(self, **kwargs: Any) -> _FakeProcess:
        process = _FakeProcess(
            pid=1000 + len(self.processes),
            ignore_terminate=(
                self.ignore_first_terminate and not self.processes
            ),
            **kwargs,
        )
        self.processes.append(process)
        return process


def _controller(
    factory: _ProcessFactory,
    *,
    healthy: Callable[[int], bool] | None = None,
    port_open: Callable[[int], bool] | None = None,
    wait_for_health: Callable[[int, float, str], bool] | None = None,
    before_restart: Callable[[], None] | None = None,
) -> OwnedApiProcessController:
    return OwnedApiProcessController(
        port=18766,
        target=lambda: None,
        session_id="launcher-session-1",
        health_check=healthy or (lambda _port: False),
        port_check=port_open or (lambda _port: False),
        wait_for_health=wait_for_health
        or (lambda _port, _timeout, _session: True),
        process_factory=factory,
        before_restart=before_restart,
        startup_timeout=1,
        stop_timeout=0.2,
    )


def test_restart_replaces_only_the_process_owned_by_this_window() -> None:
    factory = _ProcessFactory()
    telemetry: list[str] = []
    controller = _controller(
        factory,
        before_restart=lambda: telemetry.append("restarting"),
    )

    assert controller.ensure_started() is True
    result = controller.restart()

    assert len(factory.processes) == 2
    old_process, new_process = factory.processes
    assert old_process.terminate_calls == 1
    assert old_process.kill_calls == 0
    assert new_process.is_alive()
    assert result == {
        "ok": True,
        "old_pid": 1000,
        "new_pid": 1001,
        "port": 18766,
        "message": "API 与飞书服务已重启，新配置已经重新加载。",
    }
    assert telemetry == ["restarting"]


def test_restart_may_kill_only_its_exact_unresponsive_process_handle() -> None:
    factory = _ProcessFactory(ignore_first_terminate=True)
    controller = _controller(factory)
    controller.ensure_started()

    controller.restart()

    old_process = factory.processes[0]
    assert old_process.terminate_calls == 1
    assert old_process.kill_calls == 1
    assert len(factory.processes) == 2


def test_compatible_api_from_another_window_is_never_adopted_or_killed() -> None:
    factory = _ProcessFactory()
    controller = _controller(factory, healthy=lambda _port: True)

    assert controller.ensure_started() is False
    assert factory.processes == []
    assert controller.owns_process is False

    with pytest.raises(ApiProcessControlError, match="另一个应用窗口"):
        controller.restart()


def test_foreign_port_listener_is_never_replaced() -> None:
    factory = _ProcessFactory()
    controller = _controller(factory, port_open=lambda _port: True)

    with pytest.raises(ApiProcessControlError, match="避免误关其他程序"):
        controller.ensure_started()

    assert factory.processes == []
    with pytest.raises(ApiProcessControlError, match="避免误关其他程序"):
        controller.restart()


def test_restart_aborts_when_another_listener_takes_the_released_port() -> None:
    factory = _ProcessFactory()
    port_state = {"open": False}
    controller = _controller(
        factory,
        port_open=lambda _port: port_state["open"],
    )
    controller.ensure_started()
    port_state["open"] = True

    with pytest.raises(ApiProcessControlError, match="没有继续启动新服务"):
        controller.restart()

    assert len(factory.processes) == 1
    assert factory.processes[0].terminate_calls == 1


def test_shutdown_never_starts_a_replacement_and_blocks_late_restart() -> None:
    factory = _ProcessFactory()
    controller = _controller(factory)
    controller.ensure_started()

    controller.shutdown()

    assert len(factory.processes) == 1
    assert not factory.processes[0].is_alive()
    with pytest.raises(ApiProcessControlError, match="正在关闭"):
        controller.restart()


def test_global_restart_api_requires_a_registered_owned_controller() -> None:
    clear_api_process_controller()
    with pytest.raises(ApiProcessControlError, match="python -m app.launcher"):
        restart_api_service()

    factory = _ProcessFactory()
    controller = _controller(factory)
    register_api_process_controller(controller)
    try:
        assert api_service_restart_available() is False
        controller.ensure_started()
        assert api_service_restart_available() is True
        assert restart_api_service()["new_pid"] == 1001
    finally:
        controller.shutdown()
        clear_api_process_controller(controller)


def test_spawn_health_check_receives_the_launcher_session_id() -> None:
    factory = _ProcessFactory()
    received: list[tuple[int, float, str]] = []
    controller = _controller(
        factory,
        wait_for_health=lambda port, timeout, session: (
            received.append((port, timeout, session)) or True
        ),
    )

    controller.ensure_started()

    assert received == [(18766, 1.0, "launcher-session-1")]


def test_process_spawn_failure_is_reported_as_a_user_facing_control_error() -> None:
    def broken_factory(**_kwargs: Any) -> _FakeProcess:
        raise OSError("CreateProcess failed")

    controller = OwnedApiProcessController(
        port=18766,
        target=lambda: None,
        session_id="launcher-session-1",
        health_check=lambda _port: False,
        port_check=lambda _port: False,
        wait_for_health=lambda _port, _timeout, _session: True,
        process_factory=broken_factory,
        startup_timeout=1,
        stop_timeout=0.2,
    )

    with pytest.raises(ApiProcessControlError, match="launcher.log"):
        controller.ensure_started()
