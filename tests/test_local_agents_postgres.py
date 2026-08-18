from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from app.ai.model_registry import LOCAL_OPENAI_COMPATIBLE, save_model
from app.db import Database
from app.services.auth import AuthService
from app.services.local_agents import LocalAgentError, LocalAgentService


POSTGRES_URL = os.environ.get("LOCAL_AGENT_POSTGRES_TEST_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set LOCAL_AGENT_POSTGRES_TEST_URL to an isolated PostgreSQL database",
)


def test_local_agent_postgres_migration_and_concurrency() -> None:
    assert POSTGRES_URL
    with psycopg.connect(POSTGRES_URL, autocommit=True) as conn:
        conn.execute(
            """
            CREATE TABLE ai_models (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                provider_type TEXT NOT NULL DEFAULT 'openai_compatible',
                api_base TEXT,
                model TEXT NOT NULL,
                api_key_encrypted TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE local_model_requests (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                request_json TEXT NOT NULL,
                response_text TEXT,
                error TEXT,
                claimed_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    db = Database(POSTGRES_URL)
    with db.connect() as conn:
        model_columns = {
            str(row["column_name"])
            for row in conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'ai_models'
                """
            ).fetchall()
        }
        request_columns = {
            str(row["column_name"])
            for row in conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'local_model_requests'
                """
            ).fetchall()
        }
    assert "local_agent_id" in model_columns
    assert {
        "agent_id",
        "attempt_id",
        "nonce",
        "lease_until",
        "result_error_code",
    }.issubset(request_columns)

    user = AuthService(db).register("postgres_agent_user", "secret123")
    service = LocalAgentService(db)
    pairing = service.start_pairing("PostgreSQL 并发设备")
    service.approve_pairing(
        str(user["id"]),
        str(pairing["pairing_id"]),
        str(pairing["user_code"]),
    )

    def exchange() -> str:
        try:
            service.exchange_pairing(str(pairing["device_code"]))
            return "accepted"
        except LocalAgentError as exc:
            return str(exc.status_code)

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _index: exchange(), range(4)))
    assert outcomes.count("accepted") == 1
    assert sorted(item for item in outcomes if item != "accepted") == ["409"] * 3

    agent_row = db.get_local_agent_pairing(str(pairing["pairing_id"]))
    scoped = db.for_user(str(user["id"]))
    agent = scoped.get_local_model_agent(str(agent_row["agent_id"]))
    model_id = save_model(
        scoped,
        name="PostgreSQL Cockpit",
        provider_type=LOCAL_OPENAI_COMPATIBLE,
        api_base="http://127.0.0.1:11798/v1",
        model="gpt-5.5",
        api_key=None,
        local_agent_id=str(agent["id"]),
    )
    request_id = scoped.create_local_model_request(
        model_id,
        {"model": "ignored", "messages": []},
    )

    def claim() -> dict[str, object] | None:
        return scoped.claim_local_agent_request(str(agent["id"]))

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _index: claim(), range(4)))
    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1
    assert claimed[0]["request_id"] == request_id
