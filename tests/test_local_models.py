from __future__ import annotations

import threading
import time
from pathlib import Path

from app.ai.failover import FailoverRewriter
from app.ai.local_browser import LocalBrowserCompatClient
from app.ai.model_registry import (
    GEMINI,
    LOCAL_OPENAI_COMPATIBLE,
    apply_model_selection,
    public_models,
    save_model,
    test_model_connection as run_model_connection_test,
)
from app.db import Database
from app.ui.local_model_bridge import (
    _browser_completion_script,
    _browser_health_click_handler,
    _browser_health_script,
    _is_bridge_owner,
    _register_bridge_owner,
    _release_bridge_owner,
    local_bridge_result_message,
)


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
        payload={"model": "qwen2.5:7b", "messages": []},
    )

    assert "http://localhost:11434/v1/chat/completions" in script
    assert "window.isSecureContext" not in script
    assert "当前页面不是 HTTPS 安全页面" not in script
    assert "targetAddressSpace" not in script
    assert "Authorization" not in script
    assert "credentials: 'omit'" in script
    assert "AbortSignal.timeout(620000)" in script


def test_local_key_cannot_be_saved_on_the_production_server(tmp_path) -> None:
    db = Database(tmp_path / "cockpit-address.db").for_user("user-a")

    try:
        save_model(
            db,
            name="Cockpit Tools",
            provider_type=LOCAL_OPENAI_COMPATIBLE,
            api_base="http://localhost:11434/v1",
            model="gpt-5.6-sol",
            api_key="agt_codex_example",
        )
    except ValueError as exc:
        assert "不能保存到生产服务器" in str(exc)
        assert "本机助手" in str(exc)
        assert "agt_codex_example" not in str(exc)
    else:
        raise AssertionError("local model key upload must fail")


def test_browser_health_probe_supports_new_and_legacy_lna_permissions() -> None:
    script = _browser_health_script(
        api_base="http://127.0.0.1:11798/v1"
    )
    click_handler = _browser_health_click_handler(
        base_element_id=13,
        probe_mode_element_id=14,
    )

    for source in (script, click_handler):
        assert "loopback-network" in source
        assert "local-network-access" in source
        assert "credentials: 'omit'" in source
        assert "targetAddressSpace" not in source
        assert "Authorization" not in source
    assert "const readInputValue = (id)" in click_handler
    assert "html?.value" in click_handler
    assert "html?.querySelector?.('input')?.value" in click_handler
    assert "getElement(id)?.modelValue" in click_handler
    assert "readInputValue(config.baseId)" in click_handler
    assert "readInputValue(config.probeModeId)" in click_handler
    assert "http://127.0.0.1:11798/health" in click_handler
    assert "probeMode !== 'cockpit_bridge'" in click_handler
    assert "emit({ok: true, kind: 'skip'})" in click_handler
    assert click_handler.count("emit(") >= 7


def test_local_bridge_errors_are_actionable() -> None:
    assert "网站设置" in local_bridge_result_message(
        {"kind": "permission_denied"}
    )
    assert "本机助手设置" in local_bridge_result_message(
        {"kind": "key_not_configured"}
    )
    assert "API Key" in local_bridge_result_message(
        {"kind": "cockpit_unauthorized"}
    )
    assert "11797" in local_bridge_result_message(
        {"kind": "cockpit_unavailable"}
    )
    assert "限流" in local_bridge_result_message(
        {"kind": "cockpit_rate_limited"}
    )


def test_local_model_test_returns_before_server_http_client(
    tmp_path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "local-test-return.db").for_user("user-a")
    model_id = _save_local_model(db)

    class _BrowserClient:
        def complete(self, *_args, **_kwargs) -> str:
            return "OK"

    class _ForbiddenServerClient:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("server must not construct OpenAICompatClient")

    monkeypatch.setattr(
        "app.ai.model_registry.build_text_client",
        lambda *_args, **_kwargs: _BrowserClient(),
    )
    monkeypatch.setattr(
        "app.ai.openai_compat.OpenAICompatClient",
        _ForbiddenServerClient,
    )

    assert run_model_connection_test(db, model_id) == "连接成功"


def test_legacy_local_credential_is_preserved_until_verified_cleanup(
    tmp_path,
) -> None:
    path = tmp_path / "local-key-cleanup.db"
    db = Database(path).for_user("user-a")
    model_id = _save_local_model(db)
    with db.connect() as conn:
        conn.execute(
            "UPDATE ai_models SET api_key_encrypted = ? WHERE id = ?",
            ("fernet:legacy-secret", model_id),
        )

    migrated = Database(path).for_user("user-a")
    assert migrated.get_ai_model(model_id)["api_key_encrypted"] == (
        "fernet:legacy-secret"
    )

    record = dict(migrated.get_ai_model(model_id) or {})
    record["api_key_encrypted"] = "plaintext-must-not-persist"
    migrated.upsert_ai_model(record)
    assert migrated.get_ai_model(model_id)["api_key_encrypted"] == (
        "fernet:legacy-secret"
    )

    assert migrated.clear_local_model_credential(model_id) is True
    assert migrated.get_ai_model(model_id)["api_key_encrypted"] == ""
    record["api_key_encrypted"] = "fernet:stale-concurrent-value"
    migrated.upsert_ai_model(record)
    assert migrated.get_ai_model(model_id)["api_key_encrypted"] == ""


def test_local_url_rejects_userinfo_and_noncanonical_loopback(tmp_path) -> None:
    db = Database(tmp_path / "strict-loopback.db").for_user("user-a")
    for url in (
        "http://user:secret@127.0.0.1:11798/v1",
        "http://127.0.0.2:11798/v1",
        "http://localhost.localdomain:11798/v1",
    ):
        try:
            save_model(
                db,
                name="非法地址",
                provider_type=LOCAL_OPENAI_COMPATIBLE,
                api_base=url,
                model="gpt-5.5",
                api_key=None,
            )
        except ValueError as exc:
            assert "本地模型地址" in str(exc)
        else:
            raise AssertionError(f"local URL must be rejected: {url}")


def test_only_one_tab_is_the_active_browser_bridge_owner() -> None:
    owner = "tab-owner-test"
    _register_bridge_owner(owner, "tab-a")
    _register_bridge_owner(owner, "tab-b")
    assert not _is_bridge_owner(owner, "tab-a")
    assert _is_bridge_owner(owner, "tab-b")

    _release_bridge_owner(owner, "tab-b")
    assert _is_bridge_owner(owner, "tab-a")
    _release_bridge_owner(owner, "tab-a")


def test_model_panel_routes_cockpit_through_the_local_browser_bridge() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "ui"
        / "panels"
        / "models.py"
    ).read_text(encoding="utf-8")

    assert '"cockpit": ("http://127.0.0.1:11798/v1", "")' in source
    assert '"://localhost:", "://127.0.0.1:", 1' in source
    assert '.replace(":11797", ":11798", 1)' in source
