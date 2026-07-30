from __future__ import annotations

import pytest

from app.accounts import save_account
from app.services.batches import BatchService


def test_unbound_account_cannot_silently_use_global_default_model(
    tmp_path,
) -> None:
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "optional-account-model.db"),
        "_data_dir": str(tmp_path / "data"),
        "ai": {
            "primary": "moonshot",
            "fallback": "moonshot",
            "moonshot": {
                "api_key": "configured-default-key",
                "api_base": "https://api.moonshot.cn/v1",
                "model": "moonshot-v1-8k",
            },
        },
        "feishu": {},
        "wechat": {},
    }
    service = BatchService(config)
    account_id = save_account(
        service.db,
        name="尚未绑定模型的公众号",
        app_id="wx-unbound-model",
        app_secret="wechat-private-secret",
        model_id="",
    )

    with pytest.raises(
        ValueError,
        match="尚未绑定文章模型.*公众号管理中选择模型",
    ):
        service.create_batch(
            topic="测试未绑定模型保护",
            source_mode="topic",
            account_ids=[account_id],
        )
