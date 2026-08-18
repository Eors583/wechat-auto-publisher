from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import launcher
from app.local_agent import (
    DEFAULT_REMOTE_URL,
    LocalAgent,
    SingleInstanceLock,
    _origin_from_remote_url,
    local_agent_self_test,
)


class _StateStore:
    def __init__(self, state=None) -> None:
        self.state = dict(state or {})

    def load(self):
        return dict(self.state)

    def save(self, state):
        self.state = dict(state)


class _CredentialStore:
    def __init__(self, key: str = "") -> None:
        self.key = key

    def load_api_key(self) -> str:
        return self.key


def test_companion_requires_production_https() -> None:
    assert _origin_from_remote_url(DEFAULT_REMOTE_URL) == (
        "https://api.bluebloodlab.cn"
    )
    with pytest.raises(ValueError, match="HTTPS"):
        _origin_from_remote_url("http://api.bluebloodlab.cn/publisher/")


def test_companion_secure_state_and_loopback_self_test(tmp_path) -> None:
    result = local_agent_self_test(tmp_path / "agent-state")
    assert result["ok"] is True
    assert str(result["loopback_bind"]).startswith("127.0.0.1:")
    assert result["remote_origin"] == "https://api.bluebloodlab.cn"


def test_companion_single_instance_lock(tmp_path) -> None:
    first = SingleInstanceLock(tmp_path / "agent.lock")
    second = SingleInstanceLock(tmp_path / "agent.lock")
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()


def test_companion_rejects_arbitrary_operations_and_network_parameters() -> None:
    valid = {
        "request_id": "request-1",
        "attempt_id": "attempt-12345678",
        "nonce": "nonce-1234567890",
        "operation": "chat.completions",
        "deadline_at": (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat(),
        "payload": {"model": "gpt-5.5", "messages": []},
    }
    assert LocalAgent._validate_job(valid)["operation"] == "chat.completions"

    for patch in (
        {"operation": "shell.execute"},
        {"url": "https://evil.example"},
        {"headers": {"Authorization": "secret"}},
    ):
        with pytest.raises(RuntimeError):
            LocalAgent._validate_job({**valid, **patch})

    with pytest.raises(RuntimeError, match="截止时间"):
        LocalAgent._validate_job(
            {
                **valid,
                "deadline_at": (datetime.now() + timedelta(minutes=5)).isoformat(),
            }
        )


def test_companion_discards_stale_repair_state_without_replacing_token(
    monkeypatch,
) -> None:
    state_store = _StateStore(
        {
            "agent_id": "old-agent",
            "agent_token": "old-token" * 8,
            "device_code": "new-device-code" * 4,
            "pairing_id": "pair-new",
            "user_code": "12345678",
        }
    )
    agent = LocalAgent(state_store=state_store, credential_store=_CredentialStore())

    def fake_post(*_args, **_kwargs):
        raise AssertionError("paired agent must not exchange a replacement token")

    monkeypatch.setattr("app.local_agent.httpx.post", fake_post)
    assert agent._exchange_pairing() is False
    assert state_store.state["agent_id"] == "old-agent"
    assert state_store.state["agent_token"] == "old-token" * 8
    assert "device_code" not in state_store.state

    with pytest.raises(RuntimeError, match="撤销旧设备"):
        agent.start_pairing()


def test_companion_caps_cockpit_response_bytes(monkeypatch) -> None:
    class _Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"x" * (16 * 1024 * 1024)
            yield b"x"

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr("app.local_agent.httpx.Client", _Client)
    agent = LocalAgent(
        state_store=_StateStore(),
        credential_store=_CredentialStore("local-key"),
    )
    job = {
        "request_id": "request-1",
        "attempt_id": "attempt-12345678",
        "nonce": "nonce-1234567890",
        "payload": {"model": "gpt-5.5", "messages": []},
    }
    result = agent._run_cockpit_job(job)
    assert result["status"] == "failed"
    assert "16 MiB" in result["error"]


def test_companion_blocks_cockpit_key_echo_before_upload(monkeypatch) -> None:
    key = "local-test-key"

    class _Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b'{"choices":[{"message":{"content":"prefix local-'
            yield b'test-key suffix"}}]}'

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr("app.local_agent.httpx.Client", _Client)
    agent = LocalAgent(
        state_store=_StateStore(),
        credential_store=_CredentialStore(key),
    )
    result = agent._run_cockpit_job(
        {
            "request_id": "request-1",
            "attempt_id": "attempt-12345678",
            "nonce": "nonce-1234567890",
            "payload": {"model": "gpt-5.5", "messages": []},
        }
    )

    assert result["status"] == "failed"
    assert result["response_text"] == ""
    assert result["error_code"] == "cockpit.credential_echo"
    assert "受保护凭据" in result["error"]
    assert key not in str(result)


def test_companion_reports_loopback_port_conflict(monkeypatch) -> None:
    agent = LocalAgent(
        state_store=_StateStore(),
        credential_store=_CredentialStore(),
    )
    monkeypatch.setattr(
        agent,
        "_start_bridge",
        lambda: (_ for _ in ()).throw(OSError("address in use")),
    )
    opened = []
    shown = []
    monkeypatch.setattr("app.local_agent.webbrowser.open", opened.append)
    monkeypatch.setattr("app.local_agent._show_startup_error", shown.append)
    assert agent.run(open_setup=True) == 2
    assert agent._last_error_code == "agent.bridge_port_in_use"
    assert opened == []
    assert shown and "11798" in shown[0]


def test_companion_preserves_token_for_proxy_401_and_revokes_for_app_code(
    monkeypatch,
) -> None:
    state_store = _StateStore(
        {"agent_id": "agent-1", "agent_token": "t" * 64}
    )
    agent = LocalAgent(
        state_store=state_store,
        credential_store=_CredentialStore(),
    )
    request = httpx.Request("POST", "https://api.bluebloodlab.cn/heartbeat")
    proxy_response = httpx.Response(401, text="proxy auth", request=request)
    monkeypatch.setattr(
        "app.local_agent.httpx.post",
        lambda *_args, **_kwargs: proxy_response,
    )
    with pytest.raises(httpx.HTTPStatusError):
        agent._heartbeat()
    assert state_store.state["agent_token"] == "t" * 64

    app_response = httpx.Response(
        401,
        json={
            "detail": {
                "code": "agent_token_invalid",
                "message": "Agent Token 无效",
            }
        },
        request=request,
    )
    monkeypatch.setattr(
        "app.local_agent.httpx.post",
        lambda *_args, **_kwargs: app_response,
    )
    assert agent._heartbeat() is False
    assert "agent_token" not in state_store.state


def test_source_autostart_command_uses_launcher_module(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    agent = LocalAgent(
        state_store=_StateStore(),
        credential_store=_CredentialStore(),
    )
    command = agent._autostart_command()
    assert " -m app.launcher --local-agent " in command
    assert DEFAULT_REMOTE_URL in command


def test_companion_sanitizes_malformed_cockpit_json(monkeypatch) -> None:
    class _Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"[]"

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr("app.local_agent.httpx.Client", _Client)
    agent = LocalAgent(
        state_store=_StateStore(),
        credential_store=_CredentialStore("local-key"),
    )
    result = agent._run_cockpit_job(
        {
            "request_id": "request-1",
            "attempt_id": "attempt-12345678",
            "nonce": "nonce-1234567890",
            "payload": {"model": "gpt-5.5", "messages": []},
        }
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "cockpit.request_failed"
    assert "JSON 结构无效" in result["error"]


def test_launcher_local_agent_branch_does_not_touch_database_or_desktop(
    monkeypatch,
) -> None:
    received = {}

    def fake_run(remote_url: str, *, open_setup: bool) -> int:
        received.update(remote_url=remote_url, open_setup=open_setup)
        return 37

    monkeypatch.setattr(sys, "argv", [
        "publisher",
        "--local-agent",
        "--open-setup",
        "--remote-url",
        DEFAULT_REMOTE_URL,
    ])
    monkeypatch.setattr(launcher, "_ensure_standard_streams", lambda: None)
    monkeypatch.setattr(launcher, "_configure_file_logging", lambda _name: None)
    monkeypatch.setattr("app.local_agent.run_local_agent", fake_run)
    monkeypatch.setattr(
        launcher,
        "database_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local agent must not inspect the database")
        ),
    )

    assert launcher.main() == 37
    assert received == {
        "remote_url": DEFAULT_REMOTE_URL.rstrip("/"),
        "open_setup": True,
    }
