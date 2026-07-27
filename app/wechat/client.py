from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

import httpx


logger = logging.getLogger(__name__)

_RETRY_SAFE_POST_PATHS = {
    "/cgi-bin/material/batchget_material",
    "/cgi-bin/draft/batchget",
    "/cgi-bin/draft/get",
    "/cgi-bin/freepublish/batchget",
    "/cgi-bin/freepublish/get",
}


class WeChatAPIError(RuntimeError):
    def __init__(self, errcode: int, errmsg: str, payload: Any = None) -> None:
        super().__init__(f"WeChat API error {errcode}: {errmsg}")
        self.errcode = errcode
        self.errmsg = errmsg
        self.payload = payload


class WeChatClient:
    def __init__(
        self,
        get_token: Callable[[], str],
        refresh_token: Callable[[], str],
        *,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self._get_token = get_token
        self._refresh_token = refresh_token
        self._retry_attempts = max(1, int(retry_attempts))
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request_once(
            method, path, params=params, json_body=json_body, files=files, data=data, refreshed=False
        )

    def _request_once(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        files: dict[str, Any] | None,
        data: dict[str, Any] | None,
        refreshed: bool,
    ) -> dict[str, Any]:
        retry_safe = method.upper() in {"GET", "HEAD"} or path in _RETRY_SAFE_POST_PATHS
        attempts = self._retry_attempts if retry_safe else 1
        for attempt in range(1, attempts + 1):
            try:
                token = self._get_token() if not refreshed else self._refresh_token()
                query = {"access_token": token}
                if params:
                    query.update(params)
                url = f"https://api.weixin.qq.com{path}"
                with httpx.Client(timeout=60.0) as client:
                    resp = client.request(
                        method,
                        url,
                        params=query,
                        json=json_body,
                        files=files,
                        data=data,
                    )
                    resp.raise_for_status()
                    # WeChat may return text/plain JSON
                    try:
                        payload = resp.json()
                    except json.JSONDecodeError:
                        payload = {"raw": resp.text}
                break
            except (httpx.TransportError, ConnectionError, OSError) as exc:
                if attempt >= attempts:
                    raise
                delay = self._retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "WeChat transport error, retrying %s %s (%s/%s) in %.1fs: %s",
                    method,
                    path,
                    attempt + 1,
                    attempts,
                    delay,
                    exc,
                )
                if delay:
                    time.sleep(delay)

        errcode = payload.get("errcode", 0) if isinstance(payload, dict) else 0
        if errcode in (40001, 42001, 40014) and not refreshed:
            return self._request_once(
                method,
                path,
                params=params,
                json_body=json_body,
                files=files,
                data=data,
                refreshed=True,
            )
        if isinstance(payload, dict) and errcode not in (0, None):
            raise WeChatAPIError(int(errcode), str(payload.get("errmsg", "")), payload)
        return payload if isinstance(payload, dict) else {"data": payload}
