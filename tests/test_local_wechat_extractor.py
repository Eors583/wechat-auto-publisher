from __future__ import annotations

import pytest

from app.local_wechat_extractor import (
    WeChatExtractionError,
    extract_wechat_article_html,
    validate_wechat_article_url,
)
from app.providers.ingest import ingest_text
from app.ui.desktop import (
    _compose_local_wechat_content,
    _local_wechat_error_message,
    _local_wechat_extraction_script,
)

ARTICLE_HTML = """
<!doctype html><html><head>
<meta property="og:title" content="一篇真实的公众号文章">
</head><body>
<div id="js_content">
  <p>第一段正文介绍了事情的背景和重要信息，内容足够完整。</p>
  <p>第二段继续说明事实、影响、原因以及读者需要知道的结论。</p>
  <p>第三段补充实施步骤与注意事项，确保正文不会被误判为空。</p>
  <img data-src="https://mmbiz.qpic.cn/example.jpg">
</div></body></html>
"""


def test_extract_wechat_article_html_uses_js_content_only() -> None:
    result = extract_wechat_article_html(
        ARTICLE_HTML + "<div>页面底部导航不属于文章</div>",
        "https://mp.weixin.qq.com/s/example",
    )

    assert result["title"] == "一篇真实的公众号文章"
    assert "第一段正文" in result["content"]
    assert "页面底部导航" not in result["content"]
    assert result["images"] == ["https://mmbiz.qpic.cn/example.jpg"]


@pytest.mark.parametrize(
    "url",
    [
        "http://mp.weixin.qq.com/s/a",
        "https://example.com/s/a",
        "https://user:pass@mp.weixin.qq.com/s/a",
        "https://mp.weixin.qq.com:443/s/a",
        "https://mp.weixin.qq.com/not-an-article",
    ],
)
def test_wechat_url_validation_rejects_noncanonical_urls(url: str) -> None:
    with pytest.raises(WeChatExtractionError, match="只支持"):
        validate_wechat_article_url(url)


def test_extract_wechat_article_rejects_environment_error_page() -> None:
    blocked = """
    <html><head><title>环境异常</title></head>
    <body><div id="js_content"><p>环境异常，请完成验证后继续访问。</p></div></body></html>
    """
    with pytest.raises(WeChatExtractionError) as error:
        extract_wechat_article_html(blocked, "https://mp.weixin.qq.com/s/blocked")
    assert error.value.code == "wechat_blocked"


def test_server_rejects_locally_submitted_environment_error_text() -> None:
    with pytest.raises(ValueError, match="环境异常"):
        ingest_text("【本机获取的参考资料】\n环境异常，请稍后重试")


def test_browser_script_calls_local_extractor_without_address_space_override() -> None:
    script = _local_wechat_extraction_script(["https://mp.weixin.qq.com/s/example"])

    assert script.strip().startswith("(async () => {")
    assert script.strip().endswith("})()")
    assert "http://127.0.0.1:11798/extract/wechat" in script
    assert "credentials: 'omit'" in script
    assert "targetAddressSpace" not in script
    assert "Authorization" not in script


def test_local_article_content_preserves_reference_boundaries() -> None:
    content = _compose_local_wechat_content(
        [
            {
                "title": "参考一",
                "source_url": "https://mp.weixin.qq.com/s/one",
                "content": "第一篇正文",
            },
            {
                "title": "参考二",
                "source_url": "https://mp.weixin.qq.com/s/two",
                "content": "第二篇正文",
            },
        ]
    )
    assert "【本机获取的参考资料 1：参考一】" in content
    assert "【本机获取的参考资料 2：参考二】" in content
    assert "来源：https://mp.weixin.qq.com/s/two" in content


def test_old_bridge_error_tells_user_to_update_exe() -> None:
    assert "旧版桥接器" in _local_wechat_error_message({"status": 404})
