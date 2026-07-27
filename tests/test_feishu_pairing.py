from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

from app.db import Database
from app.feishu.pairing import (
    PAIRING_SETTING_KEY,
    consume_pairing_code,
    create_pairing_code,
    pairing_status,
)
from app.feishu.settings import public_feishu_settings, save_feishu_settings


def _configured_db(tmp_path) -> Database:
    db = Database(tmp_path / "app.db")
    save_feishu_settings(
        db,
        enabled=True,
        app_id="cli_pairing",
        app_secret="secret-value",
        allow_all=False,
        allowed_open_ids=["ou_existing"],
        agent_model_id="model-1",
    )
    return db


def test_pairing_code_is_only_stored_as_a_salted_hash(tmp_path) -> None:
    db = _configured_db(tmp_path)

    pairing = create_pairing_code(db)
    stored = json.loads(db.get_setting(PAIRING_SETTING_KEY) or "{}")

    assert "code" not in stored
    assert stored["code_hash"] != pairing["code"]
    assert stored["salt"]
    assert stored["hash_algorithm"] == "pbkdf2_sha256"
    assert int(stored["hash_iterations"]) > 1
    assert pairing_status(db)["status"] == "waiting"


def test_pairing_code_binds_user_and_can_only_be_consumed_once(tmp_path) -> None:
    db = _configured_db(tmp_path)
    pairing = create_pairing_code(db)

    assert consume_pairing_code(
        db,
        text=pairing["message"],
        open_id="ou_new_user",
        chat_id="oc_test_chat",
    )
    assert not consume_pairing_code(
        db,
        text=pairing["message"],
        open_id="ou_second_user",
        chat_id="oc_other_chat",
    )

    settings = public_feishu_settings(db)
    assert settings["allow_all"] is False
    assert settings["allowed_open_ids"] == ["ou_existing", "ou_new_user"]
    status = pairing_status(db)
    assert status["status"] == "used"
    assert status["bound_open_id"] == "ou_new_user"


def test_expired_pairing_code_is_rejected(tmp_path) -> None:
    db = _configured_db(tmp_path)
    pairing = create_pairing_code(db)
    stored = json.loads(db.get_setting(PAIRING_SETTING_KEY) or "{}")
    stored["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    db.set_setting(PAIRING_SETTING_KEY, json.dumps(stored))

    assert not consume_pairing_code(
        db,
        text=pairing["code"],
        open_id="ou_late_user",
    )
    assert "ou_late_user" not in public_feishu_settings(db)["allowed_open_ids"]
    assert pairing_status(db)["status"] == "expired"


def test_concurrent_pairing_attempts_have_exactly_one_winner(tmp_path) -> None:
    db = _configured_db(tmp_path)
    pairing = create_pairing_code(db)
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, bool]] = []
    outcome_lock = threading.Lock()

    def attempt(open_id: str) -> None:
        barrier.wait()
        accepted = consume_pairing_code(
            db,
            text=pairing["code"],
            open_id=open_id,
        )
        with outcome_lock:
            outcomes.append((open_id, accepted))

    threads = [
        threading.Thread(target=attempt, args=("ou_first",)),
        threading.Thread(target=attempt, args=("ou_second",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(outcomes) == 2
    winners = [open_id for open_id, accepted in outcomes if accepted]
    assert len(winners) == 1
    allowed = public_feishu_settings(db)["allowed_open_ids"]
    assert winners[0] in allowed
    assert len(set(allowed) & {"ou_first", "ou_second"}) == 1
