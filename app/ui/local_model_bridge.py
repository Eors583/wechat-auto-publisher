from __future__ import annotations

import json
import uuid
from typing import Any

from nicegui import run, ui

from app.ai.local_browser import local_chat_completions_url
from app.ai.model_registry import (
    LOCAL_OPENAI_COMPATIBLE,
    decrypt_api_key,
)
from app.services.failures import sanitize_failure_text
from app.ui.lifecycle import client_timer
from app.ui.state import AppState


def _browser_completion_script(
    *,
    api_base: str,
    api_key: str,
    payload: dict[str, Any],
) -> str:
    config = json.dumps(
        {
            "url": local_chat_completions_url(api_base),
            "apiKey": api_key,
            "payload": payload,
        },
        ensure_ascii=False,
    )
    return f"""
return await (async (config) => {{
  if (!window.isSecureContext) {{
    return {{
      ok: false,
      error: '当前页面不是 HTTPS 安全页面，Chrome/Edge 不允许公网 HTTP 页面访问本机模型。请从生产 HTTPS 地址打开后重试。',
    }};
  }}
  try {{
    const headers = {{'Content-Type': 'application/json'}};
    if (config.apiKey) headers.Authorization = `Bearer ${{config.apiKey}}`;
    const response = await fetch(config.url, {{
      method: 'POST',
      mode: 'cors',
      targetAddressSpace: 'local',
      credentials: 'omit',
      headers,
      body: JSON.stringify(config.payload),
      signal: AbortSignal.timeout(600000),
    }});
    const raw = await response.text();
    if (!response.ok) {{
      return {{ok: false, error: `本地模型 HTTP ${{response.status}}：${{raw.slice(0, 500)}}`}};
    }}
    let data;
    try {{ data = JSON.parse(raw); }}
    catch (_) {{ return {{ok: false, error: '本地模型没有返回合法 JSON'}}; }}
    const content = data?.choices?.[0]?.message?.content;
    if (!content || !String(content).trim()) {{
      return {{ok: false, error: '本地模型返回内容为空或不兼容 OpenAI Chat Completions'}};
    }}
    return {{ok: true, content: String(content)}};
  }} catch (error) {{
    return {{
      ok: false,
      error: `无法从当前浏览器访问本地模型：${{error?.message || error}}。请允许当前网页访问本地网络，并在本地 API 中允许当前网站跨域访问（Authorization、Content-Type 和 Private Network）。`,
    }};
  }}
}})({config});
"""


def install_local_model_bridge(state: AppState) -> Any:
    """Let background workers use this authenticated user's localhost model."""

    owner_client = ui.context.client
    bridge_id = uuid.uuid4().hex
    busy = False

    async def process_one_request() -> None:
        nonlocal busy
        if busy or bool(getattr(owner_client, "is_deleted", False)):
            return
        busy = True
        request: dict[str, Any] | None = None
        try:
            request = await run.io_bound(
                lambda: state.db.claim_local_model_request(bridge_id)
            )
            if not request:
                return
            model = await run.io_bound(
                lambda: state.db.get_ai_model(
                    str(request.get("model_id") or "")
                )
            )
            if (
                not model
                or str(model.get("provider_type") or "")
                != LOCAL_OPENAI_COMPATIBLE
            ):
                raise RuntimeError("本地模型配置不存在或已被删除")
            api_key = decrypt_api_key(
                str(model.get("api_key_encrypted") or "")
            )
            result = await owner_client.run_javascript(
                _browser_completion_script(
                    api_base=str(model.get("api_base") or ""),
                    api_key=api_key,
                    payload=dict(request.get("request") or {}),
                ),
                timeout=610,
            )
            if not isinstance(result, dict) or not bool(result.get("ok")):
                raise RuntimeError(
                    str((result or {}).get("error") or "本地模型浏览器桥接失败")
                    if isinstance(result, dict)
                    else "本地模型浏览器桥接失败"
                )
            await run.io_bound(
                lambda: state.db.complete_local_model_request(
                    str(request["id"]),
                    bridge_id,
                    response_text=str(result.get("content") or ""),
                )
            )
        except Exception as exc:  # noqa: BLE001
            if request:
                safe_error = sanitize_failure_text(exc)

                def finish_with_error() -> None:
                    state.db.complete_local_model_request(
                        str(request["id"]),
                        bridge_id,
                        error=safe_error,
                    )

                await run.io_bound(
                    finish_with_error
                )
        finally:
            busy = False

    return client_timer(1.0, process_one_request, immediate=True)


__all__ = ["_browser_completion_script", "install_local_model_bridge"]
