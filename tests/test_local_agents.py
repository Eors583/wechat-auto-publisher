from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ai.model_registry import LOCAL_OPENAI_COMPATIBLE, save_model
from app.db import Database
from app.services.auth import AuthService, token_hash
from app.services.local_agents import LocalAgentError, LocalAgentService


def _user(db: Database, username: str) -> dict[str, str]:
    return AuthService(db).register(username, "secret123")


def _paired_agent(
    db: Database,
    user_id: str,
    *,
    name: str = "测试电脑",
) -> tuple[dict[str, str], dict[str, object]]:
    service = LocalAgentService(db)
    started = service.start_pairing(name)
    service.approve_pairing(
        user_id,
        str(started["pairing_id"]),
        str(started["user_code"]),
    )
    token = service.exchange_pairing(str(started["device_code"]))
    agent = service.authenticate_agent(str(token["agent_token"]))
    return token, agent


def test_pairing_stores_only_hashes_and_token_is_single_use(tmp_path) -> None:
    db = Database(tmp_path / "pairing.db")
    user = _user(db, "pair_user")
    service = LocalAgentService(db)
    started = service.start_pairing("我的电脑")

    row = db.get_local_agent_pairing(str(started["pairing_id"]))
    assert row is not None
    assert row["device_code_hash"] == token_hash(str(started["device_code"]))
    assert str(started["device_code"]) not in row.values()
    assert str(started["user_code"]) not in row.values()
    assert "user_code=" not in str(started["verification_uri_complete"])

    approved = service.approve_pairing(
        str(user["id"]),
        str(started["pairing_id"]),
        str(started["user_code"]),
    )
    assert approved["ok"] is True
    exchanged = service.exchange_pairing(str(started["device_code"]))
    assert exchanged["agent_token"]

    stored = db.find_local_model_agent_by_token_hash(
        token_hash(str(exchanged["agent_token"]))
    )
    assert stored is not None
    assert str(exchanged["agent_token"]) not in stored.values()
    with pytest.raises(LocalAgentError) as replay:
        service.exchange_pairing(str(started["device_code"]))
    assert replay.value.status_code == 409


def test_pairing_rejects_invalid_code_format_and_fast_polling(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.services.local_agents.secrets.randbelow", lambda _n: 0)
    db = Database(tmp_path / "pairing-format.db")
    user = _user(db, "format_user")
    service = LocalAgentService(db)
    started = service.start_pairing("格式测试")
    assert started["user_code"] == "00000000"
    with pytest.raises(LocalAgentError) as invalid:
        service.approve_pairing(
            str(user["id"]),
            str(started["pairing_id"]),
            "abcdefgh",
        )
    assert invalid.value.status_code == 400

    with pytest.raises(LocalAgentError) as pending:
        service.exchange_pairing(str(started["device_code"]))
    assert pending.value.status_code == 202
    with pytest.raises(LocalAgentError) as too_fast:
        service.exchange_pairing(str(started["device_code"]))
    assert too_fast.value.status_code == 429


def test_pairing_locks_after_five_wrong_codes(tmp_path) -> None:
    db = Database(tmp_path / "pairing-lock.db")
    user = _user(db, "lock_user")
    service = LocalAgentService(db)
    started = service.start_pairing("锁定测试")
    wrong_code = "11111111" if started["user_code"] == "00000000" else "00000000"

    for attempt in range(5):
        with pytest.raises(LocalAgentError) as error:
            service.approve_pairing(
                str(user["id"]),
                str(started["pairing_id"]),
                wrong_code,
            )
        assert error.value.status_code == (423 if attempt == 4 else 400)
    row = db.get_local_agent_pairing(str(started["pairing_id"]))
    assert row["status"] == "locked"


def test_pairing_expiry_is_enforced(tmp_path) -> None:
    db = Database(tmp_path / "pairing-expiry.db")
    user = _user(db, "expiry_user")
    service = LocalAgentService(db)
    started = service.start_pairing("过期测试")
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(
        timespec="microseconds"
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE local_agent_pairings SET expires_at = ? WHERE id = ?",
            (expired, str(started["pairing_id"])),
        )
    with pytest.raises(LocalAgentError) as error:
        service.approve_pairing(
            str(user["id"]),
            str(started["pairing_id"]),
            str(started["user_code"]),
        )
    assert error.value.status_code == 410


def test_agent_jobs_are_bound_leased_and_idempotent(tmp_path) -> None:
    db = Database(tmp_path / "agent-jobs.db")
    user = _user(db, "job_user")
    token, agent = _paired_agent(db, str(user["id"]))
    scoped = db.for_user(str(user["id"]))
    model_id = save_model(
        scoped,
        name="Cockpit gpt-5.5",
        provider_type=LOCAL_OPENAI_COMPATIBLE,
        api_base="http://127.0.0.1:11798/v1",
        model="gpt-5.5",
        api_key=None,
        local_agent_id=str(agent["id"]),
    )
    with pytest.raises(ValueError, match="仅支持 Cockpit Tools"):
        save_model(
            scoped,
            name="不支持的 Companion Ollama",
            provider_type=LOCAL_OPENAI_COMPATIBLE,
            api_base="http://127.0.0.1:11434/v1",
            model="qwen2.5:7b",
            api_key=None,
            local_agent_id=str(agent["id"]),
        )
    request_id = scoped.create_local_model_request(
        model_id,
        {"model": "unbound-model", "messages": [{"role": "user", "content": "OK"}]},
    )

    assert scoped.claim_local_model_request("browser-tab") is None
    service = LocalAgentService(db)
    authenticated = service.authenticate_agent(str(token["agent_token"]))
    job = service.claim_job(authenticated, wait_seconds=0)
    assert job is not None
    assert job["request_id"] == request_id
    assert job["operation"] == "chat.completions"
    assert job["payload"]["model"] == "gpt-5.5"
    assert "url" not in job and "headers" not in job and "agent_id" not in job
    assert service.renew_lease(
        authenticated,
        request_id,
        str(job["attempt_id"]),
        str(job["nonce"]),
    )["ok"] is True
    assert scoped.get_local_model_agent(str(agent["id"]))["last_seen_at"]

    accepted = service.submit_result(
        authenticated,
        request_id,
        attempt_id=str(job["attempt_id"]),
        nonce=str(job["nonce"]),
        status="completed",
        response_text="OK",
        error_code="",
        error="",
    )
    assert accepted["result"] == "accepted"
    duplicate = service.submit_result(
        authenticated,
        request_id,
        attempt_id=str(job["attempt_id"]),
        nonce=str(job["nonce"]),
        status="completed",
        response_text="OK",
        error_code="",
        error="",
    )
    assert duplicate["result"] == "duplicate"
    assert scoped.get_local_model_request(request_id)["response_text"] == "OK"

    empty_id = scoped.create_local_model_request(
        model_id,
        {"model": "ignored", "messages": []},
    )
    empty_job = service.claim_job(authenticated, wait_seconds=0)
    with pytest.raises(LocalAgentError) as empty_result:
        service.submit_result(
            authenticated,
            empty_id,
            attempt_id=str(empty_job["attempt_id"]),
            nonce=str(empty_job["nonce"]),
            status="completed",
            response_text="   ",
            error_code="",
            error="",
        )
    assert empty_result.value.code == "result_empty"

    failed_id = scoped.create_local_model_request(
        model_id,
        {"model": "ignored", "messages": []},
    )
    failed_job = service.claim_job(authenticated, wait_seconds=0)
    service.submit_result(
        authenticated,
        failed_id,
        attempt_id=str(failed_job["attempt_id"]),
        nonce=str(failed_job["nonce"]),
        status="failed",
        response_text="",
        error_code="cockpit.rate_limited",
        error="本机限流",
    )
    assert scoped.get_local_model_request(failed_id)["result_error_code"] == (
        "cockpit.rate_limited"
    )

    with pytest.raises(ValueError, match="不允许"):
        scoped.create_local_model_request(
            model_id,
            {"model": "gpt-5.5", "messages": [], "url": "https://evil.example"},
        )


def test_timeout_cleanup_cannot_overwrite_completed_agent_result(tmp_path) -> None:
    db = Database(tmp_path / "agent-timeout-race.db")
    user = _user(db, "timeout_race_user")
    token, agent = _paired_agent(db, str(user["id"]))
    scoped = db.for_user(str(user["id"]))
    model_id = save_model(
        scoped,
        name="Cockpit timeout race",
        provider_type=LOCAL_OPENAI_COMPATIBLE,
        api_base="http://127.0.0.1:11798/v1",
        model="gpt-5.5",
        api_key=None,
        local_agent_id=str(agent["id"]),
    )
    request_id = scoped.create_local_model_request(
        model_id,
        {"model": "gpt-5.5", "messages": [{"role": "user", "content": "OK"}]},
    )
    service = LocalAgentService(db)
    authenticated = service.authenticate_agent(str(token["agent_token"]))
    job = service.claim_job(authenticated, wait_seconds=0)
    service.submit_result(
        authenticated,
        request_id,
        attempt_id=str(job["attempt_id"]),
        nonce=str(job["nonce"]),
        status="completed",
        response_text="OK",
        error_code="",
        error="",
    )

    scoped.fail_local_model_request(
        request_id,
        "late timeout cleanup",
        error_code="agent.timeout",
    )

    request = scoped.get_local_model_request(request_id)
    assert request["status"] == "completed"
    assert request["response_text"] == "OK"
    assert request["error"] == ""


def test_expired_lease_gets_new_attempt_and_rejects_old_result(tmp_path) -> None:
    db = Database(tmp_path / "agent-lease.db")
    user = _user(db, "lease_user")
    token, agent = _paired_agent(db, str(user["id"]))
    scoped = db.for_user(str(user["id"]))
    model_id = save_model(
        scoped,
        name="Cockpit",
        provider_type=LOCAL_OPENAI_COMPATIBLE,
        api_base="http://127.0.0.1:11798/v1",
        model="gpt-5.5",
        api_key=None,
        local_agent_id=str(agent["id"]),
    )
    request_id = scoped.create_local_model_request(
        model_id,
        {"model": "gpt-5.5", "messages": []},
    )
    service = LocalAgentService(db)
    authenticated = service.authenticate_agent(str(token["agent_token"]))
    first = service.claim_job(authenticated, wait_seconds=0)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(
        timespec="microseconds"
    )
    with scoped.connect() as conn:
        conn.execute(
            "UPDATE local_model_requests SET lease_until = ? WHERE id = ?",
            (expired, request_id),
        )
    second = service.claim_job(authenticated, wait_seconds=0)
    assert second["attempt_id"] != first["attempt_id"]
    assert second["nonce"] != first["nonce"]
    with pytest.raises(LocalAgentError) as stale:
        service.submit_result(
            authenticated,
            request_id,
            attempt_id=str(first["attempt_id"]),
            nonce=str(first["nonce"]),
            status="completed",
            response_text="old",
            error_code="",
            error="",
        )
    assert stale.value.status_code == 409


def test_agents_and_models_are_isolated_and_revocation_unbinds(tmp_path) -> None:
    db = Database(tmp_path / "agent-isolation.db")
    user_a = _user(db, "agent_alice")
    user_b = _user(db, "agent_bob")
    _token, agent = _paired_agent(db, str(user_a["id"]))
    alice = db.for_user(str(user_a["id"]))
    bob = db.for_user(str(user_b["id"]))
    assert bob.get_local_model_agent(str(agent["id"])) is None

    with pytest.raises(ValueError):
        save_model(
            bob,
            name="越权模型",
            provider_type=LOCAL_OPENAI_COMPATIBLE,
            api_base="http://127.0.0.1:11798/v1",
            model="gpt-5.5",
            api_key=None,
            local_agent_id=str(agent["id"]),
        )
    model_id = save_model(
        alice,
        name="Alice Cockpit",
        provider_type=LOCAL_OPENAI_COMPATIBLE,
        api_base="http://127.0.0.1:11798/v1",
        model="gpt-5.5",
        api_key=None,
        local_agent_id=str(agent["id"]),
    )
    request_id = alice.create_local_model_request(
        model_id,
        {"model": "gpt-5.5", "messages": []},
    )
    assert alice.get_ai_model(model_id)["local_agent_id"] == agent["id"]
    assert LocalAgentService(db).revoke_agent(
        str(user_a["id"]),
        str(agent["id"]),
    )["revoked"] is True
    assert alice.get_ai_model(model_id)["local_agent_id"] is None
    revoked_request = alice.get_local_model_request(request_id)
    assert revoked_request["status"] == "failed"
    assert revoked_request["result_error_code"] == "agent.revoked"
    with pytest.raises(LocalAgentError) as renamed:
        LocalAgentService(db).rename_agent(
            str(user_a["id"]),
            str(agent["id"]),
            "不应成功",
        )
    assert renamed.value.status_code == 404
