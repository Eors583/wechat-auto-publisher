from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from app.services.followed_content import (
    ARTICLE_SOURCE_LABELS,
    FollowedContentService,
    group_articles,
)
from app.ui.image_proxy import wechat_image_proxy_url
from app.ui.state import set_button_loading


ARTICLE_LOAD_STEP = 8
ARTICLE_LOAD_MAX = 100


def followed_article_fetch_error_message(error: Any) -> str:
    """Return a safe, actionable message for the persistent failure dialog."""

    text = str(error or "").strip()
    safe = re.sub(r"(?i)(token=)[^&\s]+", r"\1••••", text)
    safe = re.sub(r"(?i)(cookie\s*[:=]\s*)[^\r\n]+", r"\1••••", safe)
    lower = safe.casefold()
    if "200013" in lower or "freq control" in lower or "频率" in safe:
        return (
            "微信公众平台暂时限制了查询频率。请稍后再试，避免连续点击；"
            "如果持续失败，也可以重新配置并验证公众号后台登录态。"
        )
    if any(
        marker in lower
        for marker in (
            "登录态",
            "200003",
            "invalid session",
            "http 401",
            "http 403",
            "非 json",
        )
    ):
        return (
            "公众号后台登录态已失效或需要重新验证。请重新登录微信公众平台，"
            "复制新的 Token 和 Cookie 后再获取文章。"
        )
    return safe[:500] if safe else "暂时无法从公众号后台获取文章，请检查登录态后重试。"


def followed_article_cover_preview_url(cover_url: str) -> str:
    """Return the local preview URL used to avoid WeChat CDN hotlink blocking."""
    value = str(cover_url or "").strip()
    return wechat_image_proxy_url(value) if value else ""


def next_followed_article_fetch_limit(
    current_count: int,
    current_limit: int,
) -> int:
    """Grow the cumulative backend search window by one page."""
    baseline = max(0, int(current_count or 0), int(current_limit or 0))
    return min(ARTICLE_LOAD_MAX, baseline + ARTICLE_LOAD_STEP)


def open_followed_articles_dialog(
    service: FollowedContentService,
    account_id: str,
    *,
    target_account_count: int,
    on_queue: Callable[[dict[str, Any], bool], None],
    on_configure_backend: Callable[[], None],
) -> None:
    """Show one followed account's recent public articles in a focused dialog."""

    account = service.get_account(account_id)
    if not account:
        ui.notify("关注公众号不存在或已删除", type="warning")
        return
    account_name = str(account.get("name") or "关注公众号")

    with ui.dialog() as dialog, ui.card().classes("w-full").style(
        "max-width:1120px;max-height:92vh;overflow-y:auto"
    ):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            with ui.column().classes("gap-0").style("min-width:0;flex:1"):
                ui.label(f"{account_name} · 近期文章").classes(
                    "text-h6 text-weight-bold"
                )
                ui.label(
                    "仅展示该公众号自己公开发布、且能验证归属的微信原文。"
                ).classes("muted")
            ui.button("关闭", on_click=dialog.close).props("flat icon=close no-caps")

        with ui.row().classes("w-full items-end gap-3 q-mt-sm"):
            days = ui.select(
                {
                    1: "今天",
                    3: "最近3天",
                    7: "最近7天",
                    30: "最近30天",
                    90: "最近90天",
                    365: "最近1年",
                    3650: "最近10年",
                },
                value=30,
                label="日期范围",
            ).classes("w-40").props("outlined stack-label")
            keyword = ui.input(
                "搜索标题或摘要"
            ).classes("col").props("outlined stack-label clearable")
            apply_btn = ui.button("应用筛选", icon="filter_alt").props(
                "outline color=teal-9 no-caps"
            )
        with ui.row().classes("w-full items-center gap-3"):
            unread = ui.switch("只看未读")
            unrewritten = ui.switch("只看未改写")
            favorite = ui.switch("只看收藏")
            refresh_btn = ui.button("获取最新文章", icon="sync").props(
                "outline color=teal-9 no-caps"
            )
            add_btn = ui.button("手动添加文章链接", icon="add_link").props(
                "flat color=teal-9 no-caps"
            )

        summary_label = ui.label("").classes("muted q-mt-sm")
        host = ui.column().classes("w-full gap-3")
        known_article_count = len(
            service.list_articles(
                account_ids=[account_id],
                days=3650,
                include_ignored=True,
                limit=500,
            )
        )
        fetch_state = {
            "limit": min(ARTICLE_LOAD_MAX, max(ARTICLE_LOAD_STEP, known_article_count)),
            "has_more": known_article_count < ARTICLE_LOAD_MAX,
        }

        def show_fetch_failure(error: Any) -> None:
            message = followed_article_fetch_error_message(error)
            with ui.dialog().props("persistent") as error_dialog, ui.card().classes(
                "w-full q-pa-lg"
            ).style("max-width:620px"):
                with ui.row().classes("w-full items-start gap-3 no-wrap"):
                    ui.icon("error_outline", size="36px", color="red-7")
                    with ui.column().classes("gap-1").style("min-width:0;flex:1"):
                        ui.label("获取公众号文章失败").classes(
                            "text-h6 text-weight-bold"
                        )
                        ui.label(message).classes("text-body1")
                        ui.label(
                            "你可以关闭后稍后重试，或者前往配置公众号后台登录态。"
                        ).classes("muted text-caption q-mt-xs")

                def configure_backend() -> None:
                    error_dialog.close()
                    dialog.close()
                    on_configure_backend()

                with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
                    ui.button("关闭", on_click=error_dialog.close).props(
                        "flat color=grey-8 no-caps"
                    )
                    ui.button(
                        "去配置登录态",
                        icon="key",
                        on_click=configure_backend,
                    ).props("unelevated color=teal-9 no-caps")
            error_dialog.open()

        def update_article(article_id: str, **updates: Any) -> None:
            service.update_article(article_id, **updates)
            render()

        def queue_article(article: dict[str, Any], *, auto_start: bool) -> None:
            dialog.close()
            on_queue(dict(article), auto_start)

        def render() -> None:
            articles = service.list_articles(
                account_ids=[account_id],
                days=int(days.value or 30),
                keyword=str(keyword.value or ""),
                unread_only=bool(unread.value),
                favorite_only=bool(favorite.value),
                unrewritten_only=bool(unrewritten.value),
            )
            groups = group_articles(articles, mode="date")
            summary_label.set_text(f"共找到 {len(articles)} 篇近期文章")
            host.clear()
            with host:
                if not groups:
                    ui.label(
                        "暂未找到近期文章。可点击“获取最新文章”，或手动添加该公众号的微信原文链接。"
                    ).classes("muted")
                    return
                for group_name, rows in groups.items():
                    ui.label(group_name).classes("text-subtitle1 text-weight-bold q-mt-sm")
                    for article in rows:
                        with ui.element("div").classes("topic-item w-full"):
                            with ui.row().classes("w-full items-start gap-3"):
                                if article.get("cover_url"):
                                    ui.image(
                                        followed_article_cover_preview_url(
                                            str(article["cover_url"])
                                        )
                                    ).props("fit=cover no-spinner").style(
                                        "width:128px;height:80px;border-radius:9px"
                                    )
                                with ui.column().classes("gap-1").style(
                                    "min-width:0;flex:1"
                                ):
                                    ui.label(str(article["title"])).classes(
                                        "text-subtitle1 text-weight-bold"
                                    )
                                    published = str(
                                        article.get("published_at")
                                        or article.get("discovered_at")
                                        or ""
                                    )[:10]
                                    source_label = ARTICLE_SOURCE_LABELS.get(
                                        str(article.get("source_channel") or ""),
                                        str(article.get("source_channel") or "公开发现"),
                                    )
                                    ui.label(
                                        " · ".join(
                                            value
                                            for value in (published, source_label)
                                            if value
                                        )
                                    ).classes("muted")
                                    if article.get("summary"):
                                        ui.label(str(article["summary"])[:180]).classes(
                                            "text-body2"
                                        )
                            with ui.row().classes("items-center gap-2 q-mt-sm"):
                                ui.link(
                                    "查看原文", str(article["url"]), new_tab=True
                                ).classes("text-teal-9")
                                ui.button(
                                    "带去工作台",
                                    on_click=lambda _=None, row=dict(article): queue_article(
                                        row, auto_start=False
                                    ),
                                ).props("flat dense color=teal-9 no-caps")
                                direct_btn = ui.button(
                                    f"直接生成（高级 · {target_account_count} 个公众号）",
                                    on_click=lambda _=None, row=dict(article): queue_article(
                                        row, auto_start=True
                                    ),
                                ).props("outline dense color=teal-9 no-caps")
                                if target_account_count <= 0:
                                    direct_btn.disable()
                                read_state = bool(article.get("is_read"))
                                favorite_state = bool(article.get("is_favorite"))
                                ui.button(
                                    "标记未读" if read_state else "标记已读",
                                    on_click=lambda _=None,
                                    article_id=str(article["id"]),
                                    value=not read_state: update_article(
                                        article_id, is_read=value
                                    ),
                                ).props("flat dense color=grey-8 no-caps")
                                ui.button(
                                    "取消收藏" if favorite_state else "收藏",
                                    icon="star" if favorite_state else "star_border",
                                    on_click=lambda _=None,
                                    article_id=str(article["id"]),
                                    value=not favorite_state: update_article(
                                        article_id, is_favorite=value
                                    ),
                                ).props("flat dense color=orange-8 no-caps")
                                ui.button(
                                    "忽略",
                                    on_click=lambda _=None,
                                    article_id=str(article["id"]): update_article(
                                        article_id, is_ignored=True
                                    ),
                                ).props("flat dense color=grey-7 no-caps")

        with ui.row().classes("w-full justify-center q-mt-sm"):
            load_more_btn = ui.button(
                "加载更多文章",
                icon="expand_more",
            ).props("outline color=teal-9 no-caps")

        if str(account.get("fetch_method") or "") == "manual":
            refresh_btn.set_visibility(False)
            load_more_btn.set_visibility(False)

        def update_load_more_button() -> None:
            if fetch_state["has_more"]:
                load_more_btn.set_text("加载更多文章")
                load_more_btn.enable()
            else:
                load_more_btn.set_text("没有更多文章了")
                load_more_btn.disable()

        async def refresh_articles() -> None:
            set_button_loading(refresh_btn, True, f"正在获取 {account_name} 的近期文章…")
            try:
                report = await run.io_bound(
                    lambda: service.discover_account(account_id)
                )
                if report.get("error"):
                    render()
                    show_fetch_failure(report["error"])
                else:
                    ui.notify(
                        f'获取完成，发现并同步 {report.get("added", 0)} 篇文章',
                        type="positive",
                    )
                    render()
                    fetch_state["has_more"] = True
                    update_load_more_button()
            except Exception as exc:  # noqa: BLE001
                show_fetch_failure(exc)
            finally:
                set_button_loading(refresh_btn, False)

        async def load_more_articles() -> None:
            before_count = len(
                service.list_articles(
                    account_ids=[account_id],
                    days=3650,
                    include_ignored=True,
                    limit=500,
                )
            )
            next_limit = next_followed_article_fetch_limit(
                before_count,
                int(fetch_state["limit"]),
            )
            if next_limit <= int(fetch_state["limit"]):
                fetch_state["has_more"] = False
                update_load_more_button()
                return
            set_button_loading(
                load_more_btn,
                True,
                f"正在获取 {account_name} 的更早文章…",
            )
            try:
                report = await run.io_bound(
                    lambda: service.discover_account(account_id, limit=next_limit)
                )
                fetch_state["limit"] = next_limit
                after_count = len(
                    service.list_articles(
                        account_ids=[account_id],
                        days=3650,
                        include_ignored=True,
                        limit=500,
                    )
                )
                new_count = max(0, after_count - before_count)
                found_count = int(report.get("found") or 0)
                fetch_state["has_more"] = (
                    next_limit < ARTICLE_LOAD_MAX and found_count >= next_limit
                )
                if report.get("error"):
                    render()
                    show_fetch_failure(report["error"])
                elif new_count:
                    ui.notify(
                        f"已加载 {new_count} 篇更早文章；若当前列表未显示，请扩大日期范围",
                        type="positive",
                    )
                elif fetch_state["has_more"]:
                    ui.notify(
                        "本次没有新增文章，将继续向更早记录翻页",
                        type="info",
                    )
                else:
                    ui.notify("已经加载完可获取的文章", type="info")
                render()
            except Exception as exc:  # noqa: BLE001
                show_fetch_failure(exc)
            finally:
                set_button_loading(load_more_btn, False)
                update_load_more_button()

        def add_article_dialog() -> None:
            with ui.dialog() as add_dialog, ui.card().classes("w-full").style(
                "max-width:680px"
            ):
                ui.label(f"添加 {account_name} 的公开文章").classes(
                    "text-h6 text-weight-bold"
                )
                url = ui.input("微信原文链接").classes("w-full").props(
                    "outlined stack-label"
                )
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("取消", on_click=add_dialog.close).props("flat no-caps")
                    save_btn = ui.button("解析并保存", icon="save").props(
                        "unelevated color=teal-9 no-caps"
                    )

                async def save() -> None:
                    set_button_loading(
                        save_btn, True, "正在读取文章标题、公众号和发布日期…"
                    )
                    try:
                        await run.io_bound(
                            lambda: service.add_article_url(
                                str(url.value or ""),
                                followed_account_id=account_id,
                                source_channel="manual",
                            )
                        )
                        add_dialog.close()
                        render()
                        ui.notify("文章已加入该公众号的近期文章", type="positive")
                    except Exception as exc:  # noqa: BLE001
                        ui.notify(f"添加失败：{exc}", type="negative", timeout=10000)
                    finally:
                        set_button_loading(save_btn, False)

                save_btn.on_click(save)
            add_dialog.open()

        apply_btn.on_click(render)
        days.on_value_change(lambda _: render())
        unread.on_value_change(lambda _: render())
        unrewritten.on_value_change(lambda _: render())
        favorite.on_value_change(lambda _: render())
        keyword.on("keydown.enter", render)
        refresh_btn.on_click(refresh_articles)
        load_more_btn.on_click(load_more_articles)
        add_btn.on_click(add_article_dialog)
        render()
        update_load_more_button()

    dialog.open()
