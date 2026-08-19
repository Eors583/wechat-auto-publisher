from __future__ import annotations

import httpx
import pytest

from app.services.wechat_layout_import import (
    clear_wechat_article_cache,
    fetch_wechat_article_layout,
    parse_wechat_article_layout,
)


WECHAT_PAGE = """
<!doctype html>
<html>
<head>
  <meta property="og:title" content="成熟排版示例">
</head>
<body>
  <a id="js_name">蓝血研究</a>
  <div id="js_content" class="rich_media_content"
       style="padding: 0 16px; font-size: 17px; line-height: 1.75; color: #333333;">
    <section style="display:flex;justify-content:center;margin:16px 0;">
      <section style="width:100%;padding:12px;background:#f5f5f5;border-radius:8px;">
        <p style="margin:0 0 18px;text-align:justify;font-size:17px;color:#333333;line-height:1.75;">
          这是一段用于判断正文排版的足够长测试内容，会按照字符权重提取字号、颜色、行高和段落间距。
        </p>
        <p style="margin:20px 0 12px;font-size:20px;color:#0052ff;line-height:1.6;font-weight:700;">
          核心论点标题
        </p>
        <blockquote style="margin:18px 0 20px;background:#f8f9fa;border-left:4px solid #ff6827;color:#555555;font-size:15px;line-height:1.8;">
          这是一段引用内容。
        </blockquote>
        <ul><li style="font-size:16px;color:#444444;line-height:2;margin-bottom:8px;">列表内容</li></ul>
        <img data-src="http://mmbiz.qpic.cn/example/640?wx_fmt=jpeg" data-w="1080" style="width:100%;">
        <a href="javascript:alert(1)" onclick="alert(1)">不安全链接</a>
        <script>alert(1)</script>
      </section>
    </section>
  </div>
</body>
</html>
"""


@pytest.fixture(autouse=True)
def _clear_article_cache() -> None:
    clear_wechat_article_cache()
    yield
    clear_wechat_article_cache()


def test_public_wechat_layout_preserves_inline_structure_and_extracts_rules() -> None:
    result = parse_wechat_article_layout(
        WECHAT_PAGE,
        source_url="https://mp.weixin.qq.com/s/example",
    )

    assert result.title == "成熟排版示例"
    assert result.account_name == "蓝血研究"
    assert 'style="display:flex;justify-content:center;margin:16px 0;"' in result.content_html
    assert "javascript:" not in result.content_html
    assert "onclick=" not in result.content_html
    assert "<script" not in result.content_html
    assert 'src="https://mmbiz.qpic.cn/example/640?wx_fmt=jpeg"' in result.content_html
    assert 'width="1080"' in result.content_html
    assert result.layout["body"]["font_size"] == "17px"
    assert result.layout["body"]["color"] == "#333333"
    assert result.layout["body"]["line_height"] == "1.75"
    assert result.layout["body"]["spacing_after"] == "18px"
    assert result.layout["body"]["alignment"] == "justify"
    assert result.layout["argument"]["font_size"] == "20px"
    assert result.layout["argument"]["color"] == "#0052ff"
    assert result.layout["argument"]["bold"] is True
    assert result.layout["argument"]["spacing_before"] == "20px"
    assert result.layout["argument"]["spacing_after"] == "12px"
    assert result.layout["quote"]["background"] == "#f8f9fa"
    assert result.layout["quote"]["border_color"] == "#ff6827"
    assert result.diagnostics["image_count"] == 1
    assert result.diagnostics["section_depth"] >= 2
    assert "max-width:677px" in result.preview_html
    assert "visibility:visible!important" in result.preview_html
    assert "opacity:1!important" in result.preview_html
    assert "sandbox=" in result.preview_html


def test_pasted_js_content_outer_html_can_be_parsed_without_page_shell() -> None:
    fragment = """
    <div id="js_content" class="rich_media_content"
         style="font-size:17px;line-height:1.75;color:#333">
      <section style="padding:12px">
        <p>这是从浏览器开发者工具复制的正文节点，长度足够用于排版提取。</p>
        <img data-src="https://mmbiz.qpic.cn/example/pasted.jpg">
      </section>
    </div>
    """

    result = parse_wechat_article_layout(
        fragment,
        source_url="https://mp.weixin.qq.com/s/pasted-example",
    )

    assert result.title == "未读取到文章标题"
    assert 'style="padding:12px"' in result.content_html
    assert 'src="https://mmbiz.qpic.cn/example/pasted.jpg"' in result.content_html
    assert result.layout["body"]["font_size"] == "17px"


def test_pasted_article_html_has_a_safe_size_limit() -> None:
    with pytest.raises(ValueError, match="500 万字符"):
        parse_wechat_article_layout(
            '<div id="js_content">' + ("字" * 5_000_001) + "</div>",
            source_url="https://mp.weixin.qq.com/s/too-large",
        )


@pytest.mark.parametrize(
    ("heading_markup", "expected_color"),
    (
        (
            '<strong><span style="font-size:22px;color:rgb(241, 110, 29) '
            '!important">橙色一级标题</span></strong>',
            "rgb(241,110,29)",
        ),
        (
            '<strong><span style="font-size:22px;color:#333;'
            '-webkit-text-fill-color:#f26b1d">橙色一级标题</span></strong>',
            "#f26b1d",
        ),
        (
            '<strong><font color="#ef7a22" style="font-size:22px">'
            "橙色一级标题</font></strong>",
            "#ef7a22",
        ),
    ),
)
def test_heading_color_is_read_from_nested_text_elements(
    heading_markup: str,
    expected_color: str,
) -> None:
    page = f"""
    <html><head><meta property="og:title" content="嵌套标题颜色"></head><body>
      <div id="js_content" class="rich_media_content"
           style="font-size:17px;color:#333;line-height:1.8">
        <p>{heading_markup}</p>
        <p>这是一段足够长的普通正文，用于确认标题内部颜色不会被外层正文默认颜色覆盖。</p>
      </div>
    </body></html>
    """

    result = parse_wechat_article_layout(
        page,
        source_url="https://mp.weixin.qq.com/s/nested-heading-color",
    )

    assert result.layout["title"]["color"] == expected_color
    assert result.layout["title"]["font_size"] == "22px"
    assert result.layout["title"]["bold"] is True


@pytest.mark.parametrize(
    "url",
    (
        "https://example.com/s/not-wechat",
        "https://mp.weixin.qq.com/cgi-bin/home",
        "javascript:alert(1)",
    ),
)
def test_layout_import_rejects_non_article_urls(url: str) -> None:
    with pytest.raises(ValueError, match="公众号|文章"):
        parse_wechat_article_layout(WECHAT_PAGE, source_url=url)


def test_fetch_uses_browser_headers_and_referer(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.append(dict(kwargs))

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> httpx.Response:
            captured[-1]["url"] = url
            return httpx.Response(
                200,
                text=WECHAT_PAGE,
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr("app.services.wechat_layout_import.httpx.Client", FakeClient)

    result = fetch_wechat_article_layout(
        "https://mp.weixin.qq.com/s/example",
        cookie="wxuin=test; pass_ticket=test-ticket",
    )

    headers = dict(captured[0]["headers"])
    assert "MicroMessenger/8.0.50" in str(headers["User-Agent"])
    assert headers["Referer"] == "https://mp.weixin.qq.com/"
    assert headers["Cookie"] == "wxuin=test; pass_ticket=test-ticket"
    assert headers["Cache-Control"] == "no-cache"
    assert captured[0]["follow_redirects"] is True
    assert len(captured) == 1
    assert result.title == "成熟排版示例"


def test_fetch_reports_wechat_captcha_without_requesting_login_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> httpx.Response:
            return httpx.Response(
                200,
                text="<html><body>captcha 环境异常</body></html>" * 40,
                request=httpx.Request("GET", url),
                extensions={"url": "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha"},
            )

    monkeypatch.setattr("app.services.wechat_layout_import.httpx.Client", FakeClient)

    with pytest.raises(ValueError, match="安全验证页") as exc_info:
        fetch_wechat_article_layout("https://mp.weixin.qq.com/s/example")
    assert "更新公众号后台登录态" not in str(exc_info.value)


def test_fetch_switches_to_reader_immediately_after_captcha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []
    reader_page = """
    <html><head><meta property="og:title" content="验证码后备文章"></head><body>
      <article><h1>验证码后备文章</h1><div class="content">
        <p>验证码出现后应立即切换公开阅读后备，不再使用其他请求头重复访问微信。</p>
      </div></article>
    </body></html>
    """

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> httpx.Response:
            requested_urls.append(url)
            if url.startswith("https://api.readgzh.site/rd?"):
                return httpx.Response(200, text=reader_page, request=httpx.Request("GET", url))
            return httpx.Response(
                200,
                text="<html><body>captcha 环境异常</body></html>" * 40,
                request=httpx.Request("GET", url),
                extensions={"url": "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha"},
            )

    monkeypatch.setattr("app.services.wechat_layout_import.httpx.Client", FakeClient)

    result = fetch_wechat_article_layout("https://mp.weixin.qq.com/s/example")

    assert result.title == "验证码后备文章"
    assert sum(url.startswith("https://mp.weixin.qq.com/") for url in requested_urls) == 1
    assert sum(url.startswith("https://api.readgzh.site/rd?") for url in requested_urls) == 1


def test_fetch_caches_successful_article_page(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = 0

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(200, text=WECHAT_PAGE, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.services.wechat_layout_import.httpx.Client", FakeClient)

    first = fetch_wechat_article_layout("https://mp.weixin.qq.com/s/cached?tracking=1")
    second = fetch_wechat_article_layout("https://mp.weixin.qq.com/s/cached?tracking=2")

    assert first.title == second.title == "成熟排版示例"
    assert requests == 1


def test_fetch_uses_public_reader_when_wechat_blocks_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []
    reader_page = """
    <html><head><meta property="og:title" content="后备读取文章"></head><body>
      <article>
        <h1>后备读取文章</h1>
        <div class="meta"><p><strong>作者</strong> 示例公众号</p></div>
        <div class="content">
          <section style="color: #123456; font-size: 17px;">
            这是通过公开读取后备出口获得的完整排版正文，保留内联样式用于提取。
          </section>
        </div>
      </article>
    </body></html>
    """

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> httpx.Response:
            requested_urls.append(url)
            if url.startswith("https://api.readgzh.site/rd?"):
                return httpx.Response(
                    200,
                    text=reader_page,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text="<html><body>captcha 环境异常</body></html>" * 40,
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr("app.services.wechat_layout_import.httpx.Client", FakeClient)

    result = fetch_wechat_article_layout("https://mp.weixin.qq.com/s/example")

    assert result.title == "后备读取文章"
    assert "#123456" in result.content_html
    assert result.diagnostics["inline_style_count"] == 1
    assert any(url.startswith("https://api.readgzh.site/rd?") for url in requested_urls)


def test_fetch_reports_http_200_shell_page_as_missing_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> httpx.Response:
            return httpx.Response(
                200,
                text="<html><body>WeChat page shell without article</body></html>" * 40,
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr("app.services.wechat_layout_import.httpx.Client", FakeClient)

    with pytest.raises(ValueError, match="不含正文|无痕窗口"):
        fetch_wechat_article_layout("https://mp.weixin.qq.com/s/example")


def test_fetch_reports_wechat_error_app_as_expired_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error_page = (
        '<html><body id="activity-detail"></body>'
        '<script src="/mmbizappmsg/zh_CN/htmledition/js/assets/error.hash.js"></script>'
        "</html>"
    ) * 20

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> httpx.Response:
            return httpx.Response(
                200,
                text=error_page,
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr("app.services.wechat_layout_import.httpx.Client", FakeClient)

    with pytest.raises(ValueError, match="短链接已经失效") as exc_info:
        fetch_wechat_article_layout("https://mp.weixin.qq.com/s/expired")
    assert "更新公众号后台登录态" not in str(exc_info.value)
