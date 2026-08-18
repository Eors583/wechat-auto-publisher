from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.services.batches import BatchService


def _app(tmp_path, *, api_token: str = ""):
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "api-local-agents.db"),
        "_db_target": str(tmp_path / "api-local-agents.db"),
        "_data_dir": str(tmp_path / "data"),
        "auth": {"required": True},
        "api": {"token": api_token},
        "ai": {},
        "feishu": {"enabled": False},
        "wechat": {},
    }
    service = BatchService(config)
    return create_api_app(config, service, start_feishu=False), service


def test_pairing_agent_auth_job_and_revocation_api(tmp_path) -> None:
    app, batch_service = _app(tmp_path)
    with TestClient(app) as client:
        started = client.post(
            "/api/v1/local-agents/pair/start",
            json={"device_name": "API 测试电脑"},
        )
        assert started.status_code == 200
        assert started.headers["Cache-Control"] == "no-store"
        pairing = started.json()
        assert "device_code" in pairing and "user_code" in pairing

        pending = client.post(
            "/api/v1/local-agents/pair/token",
            json={"device_code": pairing["device_code"]},
        )
        assert pending.status_code == 202
        assert pending.headers["Cache-Control"] == "no-store"
        assert pending.json()["status"] == "authorization_pending"

        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "agent_api_user", "password": "secret123"},
        ).json()
        user_id = registered["user"]["id"]
        user_headers = {"Authorization": f"Bearer {registered['token']}"}
        approved = client.post(
            "/api/v1/local-agents/pair/approve",
            headers=user_headers,
            json={
                "pairing_id": pairing["pairing_id"],
                "user_code": pairing["user_code"],
            },
        )
        assert approved.status_code == 200

        exchanged = client.post(
            "/api/v1/local-agents/pair/token",
            json={"device_code": pairing["device_code"]},
        )
        assert exchanged.status_code == 200
        assert exchanged.headers["Cache-Control"] == "no-store"
        agent_token = exchanged.json()["agent_token"]
        agent_id = exchanged.json()["agent_id"]
        agent_headers = {"Authorization": f"Bearer {agent_token}"}

        assert client.post(
            "/api/v1/local-agents/heartbeat",
            headers=user_headers,
            json={"cockpit_status": "ready"},
        ).status_code == 401
        assert client.get(
            "/api/v1/local-agents",
            headers=agent_headers,
        ).status_code == 401

        heartbeat = client.post(
            "/api/v1/local-agents/heartbeat",
            headers=agent_headers,
            json={"cockpit_status": "ready", "last_error_code": ""},
        )
        assert heartbeat.status_code == 200
        listed = client.get(
            "/api/v1/local-agents",
            headers=user_headers,
        ).json()
        assert listed[0]["id"] == agent_id
        assert "token_hash" not in listed[0]
        assert "owner_user_id" not in listed[0]

        saved = client.post(
            "/api/v1/models",
            headers=user_headers,
            json={
                "name": "API Cockpit",
                "provider_type": "local_openai_compatible",
                "api_base": "http://127.0.0.1:11798/v1",
                "model": "gpt-5.5",
                "api_key": None,
                "local_agent_id": agent_id,
                "enabled": True,
            },
        )
        assert saved.status_code == 200
        model_id = saved.json()["id"]
        request_id = batch_service.db.for_user(user_id).create_local_model_request(
            model_id,
            {
                "model": "gpt-5.5",
                "messages": [{"role": "user", "content": "OK"}],
            },
        )
        claimed = client.post(
            "/api/v1/local-agents/jobs/claim?wait=0",
            headers=agent_headers,
        )
        assert claimed.status_code == 200
        job = claimed.json()
        assert job["request_id"] == request_id
        assert "owner_user_id" not in job and "agent_id" not in job
        completed = client.post(
            f"/api/v1/local-agents/jobs/{request_id}/result",
            headers=agent_headers,
            json={
                "attempt_id": job["attempt_id"],
                "nonce": job["nonce"],
                "status": "completed",
                "response_text": "OK",
                "error_code": "",
                "error": "",
            },
        )
        assert completed.status_code == 200
        assert completed.json()["result"] == "accepted"
        assert client.post(
            "/api/v1/local-agents/jobs/claim?wait=0",
            headers=agent_headers,
        ).status_code == 204

        other = client.post(
            "/api/v1/auth/register",
            json={"username": "agent_other", "password": "secret123"},
        ).json()
        other_headers = {"Authorization": f"Bearer {other['token']}"}
        assert client.delete(
            f"/api/v1/local-agents/{agent_id}",
            headers=other_headers,
        ).status_code == 404
        assert client.delete(
            f"/api/v1/local-agents/{agent_id}",
            headers=user_headers,
        ).status_code == 200
        assert client.post(
            "/api/v1/local-agents/heartbeat",
            headers=agent_headers,
            json={"cockpit_status": "ready"},
        ).status_code == 401


def test_local_model_api_rejects_key_without_echoing_it(tmp_path) -> None:
    app, _service = _app(tmp_path)
    with TestClient(app) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "no_key_upload", "password": "secret123"},
        ).json()
        headers = {"Authorization": f"Bearer {registered['token']}"}
        secret = "must-never-be-returned"
        response = client.post(
            "/api/v1/models",
            headers=headers,
            json={
                "name": "Local",
                "provider_type": "local_openai_compatible",
                "api_base": "http://127.0.0.1:11798/v1",
                "model": "gpt-5.5",
                "api_key": secret,
                "enabled": True,
            },
        )
        assert response.status_code == 400
        assert secret not in response.text
        assert "本机助手" in response.text


def test_pairing_rate_limit_and_legacy_api_token_owner(tmp_path) -> None:
    app, _service = _app(tmp_path, api_token="legacy-admin-token")
    with TestClient(app) as client:
        starts = [
            client.post(
                "/api/v1/local-agents/pair/start",
                json={"device_name": f"rate-{index}"},
            )
            for index in range(7)
        ]
        assert [item.status_code for item in starts[:6]] == [200] * 6
        assert starts[6].status_code == 429
        assert starts[6].headers["Retry-After"] == "60"

        pairing = starts[0].json()
        legacy_headers = {"Authorization": "Bearer legacy-admin-token"}
        approved = client.post(
            "/api/v1/local-agents/pair/approve",
            headers=legacy_headers,
            json={
                "pairing_id": pairing["pairing_id"],
                "user_code": pairing["user_code"],
            },
        )
        assert approved.status_code == 200
        exchanged = client.post(
            "/api/v1/local-agents/pair/token",
            json={"device_code": pairing["device_code"]},
        )
        assert exchanged.status_code == 200


def test_pairing_rate_limit_uses_last_forwarded_hop_from_local_proxy(tmp_path) -> None:
    app, _service = _app(tmp_path)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        spoofed_chain = {"X-Forwarded-For": "198.51.100.10, 203.0.113.7"}
        starts = [
            client.post(
                "/api/v1/local-agents/pair/start",
                headers=spoofed_chain,
                json={"device_name": f"proxy-rate-{index}"},
            )
            for index in range(7)
        ]
        assert [item.status_code for item in starts[:6]] == [200] * 6
        assert starts[6].status_code == 429


def test_pair_token_random_codes_hit_source_ceiling(tmp_path) -> None:
    app, _service = _app(tmp_path)
    with TestClient(app) as client:
        responses = [
            client.post(
                "/api/v1/local-agents/pair/token",
                json={"device_code": f"{'x' * 32}{index:04d}"},
            )
            for index in range(121)
        ]
        assert all(item.status_code != 429 for item in responses[:120])
        assert responses[120].status_code == 429
        assert responses[120].headers["Retry-After"] == "3"
