from __future__ import annotations


class WeChatHTTPError(RuntimeError):
    """Sanitized HTTP failure that never includes token-bearing request URLs."""

    def __init__(self, status_code: int) -> None:
        self.status_code = int(status_code)
        super().__init__(f"WeChat gateway HTTP {self.status_code}")


def friendly_wechat_error(error: Exception | str) -> str:
    """Translate common official API and relay failures without echoing secrets."""

    message = str(error or "").strip()
    lower = message.casefold()
    status_code = getattr(error, "status_code", None)
    if status_code in {401, 403} or "gateway http 401" in lower or "gateway http 403" in lower:
        return "微信云中转用户名或密码错误"
    if status_code == 404 or "gateway http 404" in lower:
        return "微信云中转地址不存在，请检查是否包含完整的 /wechat-relay 路径"
    if status_code in {502, 503, 504} or any(
        f"gateway http {code}" in lower for code in (502, 503, 504)
    ):
        return "微信云中转暂时无法连接微信服务器，请稍后重试"
    if "40125" in message or "invalid appsecret" in lower:
        return "公众号 AppSecret 无效，请更新公众号凭证"
    if "40013" in message or "invalid appid" in lower:
        return "公众号 AppID 无效，请检查是否完整填写"
    if "40164" in message or "invalid ip" in lower or "whitelist" in lower:
        return "云服务器固定出口 IP 尚未加入该公众号的开发者 IP 白名单"
    if "48001" in message or "api unauthorized" in lower:
        return "公众号没有草稿接口权限，请确认账号类型和接口权限"
    if "10054" in message:
        return "微信服务器临时断开连接，请稍后重试"
    if "certificate" in lower or "ssl" in lower:
        return "微信云中转 HTTPS 证书校验失败"
    if any(
        marker in lower
        for marker in ("timed out", "timeout", "connecterror", "connection refused")
    ) or "10060" in message:
        return "无法连接微信云中转，请检查服务器、域名和防火墙"
    return message or "微信接口调用失败"


__all__ = ["WeChatHTTPError", "friendly_wechat_error"]
