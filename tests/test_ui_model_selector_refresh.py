from __future__ import annotations

from typing import Any

from app.ai.image_providers import IMAGE_ALIBABA
from app.ai.model_registry import OPENAI_COMPATIBLE
from app.db import Database
from app.ui.state import AppState


class _FakeClient:
    def __init__(self) -> None:
        self.is_deleted = False


class _FakeSelect:
    def __init__(
        self,
        *,
        value: str | None = None,
        client: Any | None = None,
        options: dict[str, str] | None = None,
    ) -> None:
        self.value = value
        self.client = client
        self.options = dict(options or {})
        self.is_deleted = False
        self.update_count = 0

    def set_options(
        self,
        options: dict[str, str],
        *,
        value: Any = ...,
    ) -> None:
        self.options = dict(options)
        if value is not ...:
            self.value = value
        self.update_count += 1


class _FakeOwner:
    def __init__(self, value: bool = True) -> None:
        self.value = value
        self.is_deleted = False


def _state(db: Database) -> AppState:
    state = AppState.__new__(AppState)
    state.config = {
        "ai": {
            "primary": "deepseek",
            "deepseek": {
                "api_key": "configured-secret",
                "api_base": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
            },
        }
    }
    state.db = db
    state.model_selects = []
    state._model_select_client = None
    return state


def _add_model(
    db: Database,
    *,
    model_id: str,
    provider_type: str,
    enabled: bool = True,
) -> None:
    db.upsert_ai_model(
        {
            "id": model_id,
            "name": model_id,
            "provider_type": provider_type,
            "api_base": "https://example.test/v1",
            "model": f"{model_id}-model",
            "api_key_encrypted": "encrypted",
            "enabled": enabled,
        }
    )


def test_registered_model_selects_refresh_by_text_and_image_purpose(
    tmp_path,
) -> None:
    db = Database(tmp_path / "selectors.db")
    _add_model(db, model_id="text-1", provider_type=OPENAI_COMPATIBLE)
    _add_model(db, model_id="image-1", provider_type=IMAGE_ALIBABA)
    state = _state(db)
    client = _FakeClient()
    text_select = _FakeSelect(value="text-1", client=client)
    image_select = _FakeSelect(value="missing-image", client=client)

    state.register_model_select(
        text_select,
        purpose="text",
        default_label="暂不绑定模型",
    )
    state.register_model_select(
        image_select,
        purpose="image",
        default_label="不配置图片生成模型",
    )
    state.refresh_model_selects()

    assert text_select.value == "text-1"
    assert "text-1" in text_select.options
    assert "config:deepseek" not in text_select.options
    assert "image-1" not in text_select.options
    assert image_select.value == ""
    assert "image-1" in image_select.options
    assert "text-1" not in image_select.options
    assert "config:deepseek" not in image_select.options

    _add_model(db, model_id="text-2", provider_type=OPENAI_COMPATIBLE)
    _add_model(db, model_id="image-2", provider_type=IMAGE_ALIBABA)
    state.refresh_model_selects()

    assert "text-2" in text_select.options
    assert "image-2" not in text_select.options
    assert "image-2" in image_select.options
    assert "text-2" not in image_select.options


def test_refresh_resets_disabled_value_and_prunes_closed_or_deleted_controls(
    tmp_path,
) -> None:
    db = Database(tmp_path / "cleanup.db")
    _add_model(db, model_id="text-1", provider_type=OPENAI_COMPATIBLE)
    state = _state(db)
    client = _FakeClient()
    active = _FakeSelect(value="text-1", client=client)
    closed = _FakeSelect(value="text-1", client=client)
    deleted = _FakeSelect(value="text-1", client=client)
    deleted.is_deleted = True
    state.register_model_select(active, purpose="text")
    state.register_model_select(
        closed,
        purpose="text",
        owner=_FakeOwner(value=False),
    )
    state.register_model_select(deleted, purpose="text")

    _add_model(
        db,
        model_id="text-1",
        provider_type=OPENAI_COMPATIBLE,
        enabled=False,
    )
    state.refresh_model_selects()

    assert active.value == ""
    assert active.update_count == 1
    assert closed.update_count == 0
    assert deleted.update_count == 0
    assert len(state.model_selects) == 1


def test_separate_page_states_never_refresh_another_clients_elements(
    tmp_path,
) -> None:
    db = Database(tmp_path / "clients.db")
    first_state = _state(db)
    second_state = _state(db)
    first_client = _FakeClient()
    second_client = _FakeClient()
    first_select = _FakeSelect(client=first_client)
    second_select = _FakeSelect(
        client=second_client,
        options={"before": "未刷新"},
    )
    first_state.register_model_select(first_select, purpose="text")
    second_state.register_model_select(second_select, purpose="text")

    _add_model(db, model_id="text-new", provider_type=OPENAI_COMPATIBLE)
    first_state.refresh_model_selects()

    assert "text-new" in first_select.options
    assert second_select.options == {"before": "未刷新"}
    assert second_select.update_count == 0

    cross_client_select = _FakeSelect(client=second_client)
    first_state.register_model_select(cross_client_select, purpose="text")
    first_state.refresh_model_selects()
    assert cross_client_select.update_count == 0
