from __future__ import annotations

import json
import threading
import uuid
from typing import Any
from urllib.parse import urlparse

from nicegui import run, ui

from app.ai.local_browser import local_chat_completions_url
from app.ai.model_registry import LOCAL_OPENAI_COMPATIBLE
from app.services.failures import sanitize_failure_text
from app.ui.lifecycle import client_timer
from app.ui.state import AppState


_BRIDGE_OWNERS: dict[str, list[str]] = {}
_BRIDGE_OWNERS_LOCK = threading.Lock()


def _register_bridge_owner(owner_user_id: str, bridge_id: str) -> None:
    with _BRIDGE_OWNERS_LOCK:
        owners = _BRIDGE_OWNERS.setdefault(owner_user_id, [])
        if bridge_id in owners:
            owners.remove(bridge_id)
        owners.append(bridge_id)


def _is_bridge_owner(owner_user_id: str, bridge_id: str) -> bool:
    with _BRIDGE_OWNERS_LOCK:
        owners = _BRIDGE_OWNERS.get(owner_user_id) or []
        return bool(owners and owners[-1] == bridge_id)


def _release_bridge_owner(owner_user_id: str, bridge_id: str) -> None:
    with _BRIDGE_OWNERS_LOCK:
        owners = _BRIDGE_OWNERS.get(owner_user_id) or []
        if bridge_id in owners:
            owners.remove(bridge_id)
        if not owners:
            _BRIDGE_OWNERS.pop(owner_user_id, None)


def _local_health_url(api_base: str) -> str:
    parsed = urlparse(str(api_base or "").strip())
    if (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.port == 11798
    ):
        return "http://127.0.0.1:11798/health"
    return f"{parsed.scheme}://{parsed.netloc}/health"


def _browser_health_script(*, api_base: str) -> str:
    """Probe the loopback helper and let Chromium show its native LNA prompt."""

    config = json.dumps(
        {"url": _local_health_url(api_base)},
        ensure_ascii=False,
    )
    return f"""
return await (async (config) => {{
  let permissionName = '';
  let permissionState = 'unsupported';
  if (navigator.permissions?.query) {{
    for (const name of ['loopback-network', 'local-network-access']) {{
      try {{
        const status = await navigator.permissions.query({{name}});
        permissionName = name;
        permissionState = status.state || 'prompt';
        break;
      }} catch (_) {{}}
    }}
  }}
  if (permissionState === 'denied') {{
    return {{
      ok: false,
      kind: 'permission_denied',
      permissionName,
      permissionState,
    }};
  }}
  try {{
    const response = await fetch(config.url, {{
      method: 'GET',
      mode: 'cors',
      credentials: 'omit',
      headers: {{Accept: 'application/json'}},
      signal: AbortSignal.timeout(8000),
    }});
    let data = {{}};
    try {{ data = await response.json(); }} catch (_) {{}}
    if (!response.ok) {{
      return {{
        ok: false,
        kind: 'bridge_http',
        status: response.status,
        permissionName,
        permissionState,
      }};
    }}
    if (data?.bridge_ready !== true) {{
      return {{ok: false, kind: 'invalid_bridge', permissionName, permissionState}};
    }}
    if (!data?.key_configured) {{
      return {{ok: false, kind: 'key_not_configured', permissionName, permissionState}};
    }}
    if (data?.cockpit_status !== 'ready') {{
      return {{
        ok: false,
        kind: 'cockpit_' + String(data?.cockpit_status || 'unavailable'),
        permissionName,
        permissionState,
      }};
    }}
    return {{
      ok: true,
      kind: 'ready',
      permissionName,
      permissionState: permissionState === 'prompt' ? 'granted' : permissionState,
    }};
  }} catch (error) {{
    return {{
      ok: false,
      kind: 'unreachable',
      permissionName,
      permissionState,
      browserError: String(error?.message || error || ''),
    }};
  }}
}})({config});
"""


def _browser_health_click_handler(
    *,
    base_element_id: int,
    probe_mode_element_id: int | None,
) -> str:
    """Run the Cockpit health request inside the user's actual click event."""

    config = json.dumps(
        {
            "baseId": base_element_id,
            "probeModeId": probe_mode_element_id,
        }
    )
    return f"""
async () => {{
  const config = {config};
  const readInputValue = (id) => {{
    const html = getHtmlElement(id);
    return String(
      html?.value ??
      html?.querySelector?.('input')?.value ??
      getElement(id)?.modelValue ??
      ''
    );
  }};
  const probeMode = config.probeModeId === null
    ? 'skip'
    : (readInputValue(config.probeModeId) || 'skip');
  const apiBase = readInputValue(config.baseId);
  if (probeMode !== 'cockpit_bridge') {{
    emit({{ok: true, kind: 'skip'}});
    return;
  }}
  let permissionName = '';
  let permissionState = 'unsupported';
  if (navigator.permissions?.query) {{
    for (const name of ['loopback-network', 'local-network-access']) {{
      try {{
        const status = await navigator.permissions.query({{name}});
        permissionName = name;
        permissionState = status.state || 'prompt';
        break;
      }} catch (_) {{}}
    }}
  }}
  if (permissionState === 'denied') {{
    emit({{ok: false, kind: 'permission_denied', permissionName, permissionState}});
    return;
  }}
  let url;
  try {{
    const parsed = new URL(apiBase);
    const loopbackHosts = new Set(['localhost', '127.0.0.1', '[::1]']);
    if (
      parsed.protocol !== 'http:' ||
      !loopbackHosts.has(parsed.hostname) ||
      !['11797', '11798'].includes(parsed.port)
    ) {{
      emit({{ok: false, kind: 'invalid_bridge'}});
      return;
    }}
    url = 'http://127.0.0.1:11798/health';
  }} catch (_) {{
    emit({{ok: false, kind: 'invalid_bridge'}});
    return;
  }}
  try {{
    const response = await fetch(url, {{
      method: 'GET',
      mode: 'cors',
      credentials: 'omit',
      headers: {{Accept: 'application/json'}},
      signal: AbortSignal.timeout(8000),
    }});
    let data = {{}};
    try {{ data = await response.json(); }} catch (_) {{}}
    if (!response.ok) {{
      emit({{ok: false, kind: 'bridge_http', status: response.status, permissionName, permissionState}});
      return;
    }}
    if (data?.bridge_ready !== true) {{
      emit({{ok: false, kind: 'invalid_bridge', permissionName, permissionState}});
      return;
    }}
    if (!data?.key_configured) {{
      emit({{ok: false, kind: 'key_not_configured', permissionName, permissionState}});
      return;
    }}
    if (data?.cockpit_status !== 'ready') {{
      emit({{
        ok: false,
        kind: 'cockpit_' + String(data?.cockpit_status || 'unavailable'),
        permissionName,
        permissionState,
      }});
      return;
    }}
    emit({{ok: true, kind: 'ready', permissionName, permissionState}});
  }} catch (error) {{
    emit({{
      ok: false,
      kind: 'unreachable',
      permissionName,
      permissionState,
      browserError: String(error?.message || error || ''),
    }});
  }}
}}
"""


def local_bridge_result_message(result: Any) -> str:
    """Translate a browser probe into an actionable, non-speculative message."""

    item = result if isinstance(result, dict) else {}
    kind = str(item.get("kind") or "unreachable")
    if kind == "permission_denied":
        return (
            "浏览器已拒绝本地网络访问。请点击地址栏左侧的网站设置，将"
            "“设备上的应用”或“本地网络访问”设为允许；若没有该项，请重置该网站权限后重试。"
        )
    if kind == "key_not_configured":
        return (
            "本机助手已启动，但尚未配置 Cockpit API Key。请先点击“打开本机助手设置”，"
            "用新 Key 验证并保存。"
        )
    if kind == "cockpit_unauthorized":
        return "本机助手中的 Cockpit API Key 已失效，请在本机设置页更换新 Key。"
    if kind == "cockpit_endpoint_not_found":
        return "Cockpit Tools 已响应，但 /v1/models 接口不存在，请确认 API 服务已启用。"
    if kind == "cockpit_rate_limited":
        return "Cockpit Tools 当前限流，请稍后再测试。"
    if kind in {"cockpit_unavailable", "cockpit_upstream_error"}:
        return "本机助手在线，但设置页中填写的 Cockpit 地址未启动或响应异常。"
    if kind == "bridge_http":
        return f"本机助手返回 HTTP {int(item.get('status') or 0)}，请重新启动本机助手后重试。"
    if kind == "invalid_bridge":
        return "11798 端口上的程序不是当前版本的本机助手，请退出冲突程序后重新启动。"
    return (
        "浏览器尚未连接到本机助手。请确认 11798 助手正在运行并允许当前网站访问"
        "“设备上的应用/本地网络”；CORS、杀毒软件或本机进程拦截也可能导致此错误。"
    )


def _browser_completion_script(
    *,
    api_base: str,
    payload: dict[str, Any],
) -> str:
    config = json.dumps(
        {
            "url": local_chat_completions_url(api_base),
            "payload": payload,
        },
        ensure_ascii=False,
    )
    return f"""
return await (async (config) => {{
  try {{
    const response = await fetch(config.url, {{
      method: 'POST',
      mode: 'cors',
      credentials: 'omit',
      headers: {{'Content-Type': 'application/json', Accept: 'application/json'}},
      body: JSON.stringify(config.payload),
      signal: AbortSignal.timeout(620000),
    }});
    const raw = await response.text();
    if (!response.ok) {{
      const statuses = {{
        401: 'Cockpit API Key 无效，请在本机助手设置页更换密钥',
        403: 'Cockpit 拒绝了当前请求，请检查本机密钥权限',
        404: 'Cockpit 接口或模型名称不存在',
        428: '本机助手尚未配置 Cockpit API Key',
        429: 'Cockpit 当前限流，请稍后重试',
        502: '本机助手在线，但设置页中填写的 Cockpit 地址不可达',
        504: 'Cockpit 模型调用超时',
      }};
      return {{
        ok: false,
        error: statuses[response.status] || `本地模型 HTTP ${{response.status}}，请查看本机助手状态`,
      }};
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
    const timedOut = error?.name === 'TimeoutError' || error?.name === 'AbortError';
    return {{
      ok: false,
      error: timedOut
        ? 'Cockpit 模型调用超过 620 秒，已停止等待'
        : '浏览器无法访问本机助手。请确认本机助手已启动，并允许当前网站访问“设备上的应用/本地网络”。',
    }};
  }}
}})({config});
"""


def install_local_model_bridge(state: AppState) -> Any:
    """Let background workers use this authenticated user's localhost model."""

    owner_client = ui.context.client
    bridge_id = uuid.uuid4().hex
    owner_user_id = str(state.db.owner_user_id or "").strip()
    _register_bridge_owner(owner_user_id, bridge_id)
    owner_client.on_connect(
        lambda: _register_bridge_owner(owner_user_id, bridge_id)
    )
    owner_client.on_disconnect(
        lambda: _release_bridge_owner(owner_user_id, bridge_id)
    )
    owner_client.on_delete(
        lambda: _release_bridge_owner(owner_user_id, bridge_id)
    )
    busy = False

    async def process_one_request() -> None:
        nonlocal busy
        if (
            busy
            or bool(getattr(owner_client, "is_deleted", False))
            or not bool(getattr(owner_client, "has_socket_connection", False))
            or not _is_bridge_owner(owner_user_id, bridge_id)
        ):
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
            result = await owner_client.run_javascript(
                _browser_completion_script(
                    api_base=str(model.get("api_base") or ""),
                    payload=dict(request.get("request") or {}),
                ),
                timeout=630,
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

                await run.io_bound(finish_with_error)
        finally:
            busy = False

    return client_timer(1.0, process_one_request, immediate=True)


__all__ = [
    "_browser_completion_script",
    "_browser_health_click_handler",
    "_browser_health_script",
    "_is_bridge_owner",
    "_register_bridge_owner",
    "_release_bridge_owner",
    "install_local_model_bridge",
    "local_bridge_result_message",
]
