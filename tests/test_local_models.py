from __future__ import annotations

import threading
import time

from app.ai.failover import FailoverRewriter
from app.ai.local_browser import LocalBrowserCompatClient
from app.ai.model_registry import (
    GEMINI,
    LOCAL_OPENAI_COMPATIBLE,
    apply_model_selection,
    public_models,
    save_model,
)
from app.db import Database
from app.ui.local_model_bridge import _browser_completion_script


def _save_local_model(db: Database, *, name: str = "我的 Ollama") -> str:
    return save_model(
        db,
        name=name,
        provider_type=LOCAL_OPENAI_COMPATIBLE,
        api_base="http://localhost:11434/v1",
        model="qwen2.5:7b",
        api_key=None,
    )


def test_local_model_is_private_and_accepts_only_loopback_urls(tmp_path) -> None:
    root = Database(tmp_path / "local-model.db")
    user_a = root.for_user("user-a")
    user_b = root.for_user("user-b")
    model_id = _save_local_model(user_a)

    visible = public_models(user_a)[0]
    assert visible["id"] == model_id
    assert visible["connection_type"] == "local"
    assert visible["has_api_key"] is False
    assert public_models(user_b) == []

    for invalid_url in (
        "http://localhost/v1",
        "https://api.example.com/v1",
        "http://192.168.1.8:11434/v1",
    ):
        try:
            save_model(
                user_a,
                name="非法本地地址",
                provider_type=LOCAL_OPENAI_COMPATIBLE,
                api_base=invalid_url,
                model="qwen2.5:7b",
                api_key=None,
            )
        except ValueError as exc:
            assert "本地模型地址" in str(exc)
        else:
            raise AssertionError(f"local model URL must be rejected: {invalid_url}")


def test_switching_to_local_does_not_reuse_an_api_provider_key(tmp_path) -> None:
    db = Database(tmp_path / "local-model-key.db").for_user("user-a")
    model_id = save_model(
        db,
        name="Gemini API",
        provider_type=GEMINI,
        api_base="",
        model="gemini-2.5-flash",
        api_key="api-provider-secret",
    )

    save_model(
        db,
        model_id=model_id,
        name="本地 Ollama",
        provider_type=LOCAL_OPENAI_COMPATIBLE,
        api_base="http://localhost:11434/v1",
        model="qwen2.5:7b",
        api_key=None,
    )

    stored = db.get_ai_model(model_id)
    assert stored is not None
    assert stored["api_key_encrypted"] == ""


def test_local_model_request_queue_is_isolated_by_user(tmp_path) -> None:
    root = Database(tmp_path / "local-model-queue.db")
    user_a = root.for_user("user-a")
    user_b = root.for_user("user-b")
    model_id = _save_local_model(user_a)
    request_id = user_a.create_local_model_request(
        model_id,
        {"model": "qwen2.5:7b", "messages": [{"role": "user", "content": "OK"}]},
    )

    assert user_b.claim_local_model_request("browser-b") is None
    claimed = user_a.claim_local_model_request("browser-a")
    assert claimed is not None
    assert claimed["id"] == request_id
    assert claimed["request"]["model"] == "qwen2.5:7b"

    user_b.complete_local_model_request(
        request_id,
        "browser-a",
        response_text="越权结果",
    )
    assert user_a.get_local_model_request(request_id)["status"] == "running"

    user_a.complete_local_model_request(
        request_id,
        "browser-a",
        response_text="OK",
    )
    completed = user_a.get_local_model_request(request_id)
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["response_text"] == "OK"


def test_local_client_waits_for_the_authenticated_browser_bridge(tmp_path) -> None:
    db = Database(tmp_path / "local-client.db").for_user("user-a")
    model_id = _save_local_model(db)
    client = LocalBrowserCompatClient(
        db=db,
        model_id=model_id,
        model="qwen2.5:7b",
        provider_name="本地 Ollama",
        timeout=3,
    )
    result: dict[str, str] = {}

    def run_completion() -> None:
        result["content"] = client.complete("只回复 OK", max_tokens=8)

    worker = threading.Thread(target=run_completion)
    worker.start()
    claimed = None
    for _ in range(30):
        claimed = db.claim_local_model_request("browser-a")
        if claimed:
            break
        time.sleep(0.05)
    assert claimed is not None
    db.complete_local_model_request(
        str(claimed["id"]),
        "browser-a",
        response_text="OK",
    )
    worker.join(timeout=3)

    assert result == {"content": "OK"}
    assert db.get_local_model_request(str(claimed["id"])) is None


def test_failover_uses_local_browser_client_for_generation(tmp_path) -> None:
    db = Database(tmp_path / "local-failover.db").for_user("user-a")
    model_id = _save_local_model(db)
    config = apply_model_selection({"ai": {}}, db, model_id)

    rewriter = FailoverRewriter(config, db=db)

    assert isinstance(rewriter._clients[model_id], LocalBrowserCompatClient)


def test_browser_bridge_posts_openai_payload_to_the_local_machine() -> None:
    script = _browser_completion_script(
        api_base="http://localhost:11434/v1",
        api_key="",
        payload={"model": "qwen2.5:7b", "messages": []},
    )

    assert "http://localhost:11434/v1/chat/completions" in script
    assert "credentials: 'omit'" in script
    assert "AbortSignal.timeout(600000)" in script
