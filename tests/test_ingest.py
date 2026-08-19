from __future__ import annotations

import pytest

from app.providers import ingest


class _Response:
    text = "<html>blocked</html>"

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get(self, _url: str) -> _Response:
        return _Response()


def test_environment_error_page_is_rejected_before_ai(monkeypatch) -> None:
    monkeypatch.setattr(ingest.httpx, "Client", _Client)
    monkeypatch.setattr(
        ingest,
        "_extract_with_trafilatura",
        lambda _html, _url: (
            "环境异常",
            "视频 小程序 赞 轻点两下取消赞 在看 轻点两下取消在看" * 3,
            [],
        ),
    )

    with pytest.raises(ValueError, match="环境异常.*未获取到真实文章正文"):
        ingest.ingest_url("https://mp.weixin.qq.com/s/blocked")
