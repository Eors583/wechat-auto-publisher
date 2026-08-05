from __future__ import annotations

from typing import Any


def format_accounts(accounts: list[dict[str, Any]]) -> str:
    lines = ["当前可用公众号："]
    lines.extend(
        f'• {item["name"]}（模型：{item.get("model_name") or "未命名"}）'
        for item in accounts
    )
    return "\n".join(lines)


def format_hot_topics(items: list[dict[str, Any]]) -> str:
    fallback_only = all(str(item.get("source")) == "fallback" for item in items)
    lines = [
        "外部热点源暂时不可用，以下是备用管理选题："
        if fallback_only
        else f"近 7 天热点（前 {len(items)} 条）："
    ]
    for index, item in enumerate(items, 1):
        title = str(item.get("title") or "").strip()
        source = str(item.get("source") or "热点资讯").strip()
        published = str(item.get("published_at") or "")[:10]
        url = str(item.get("url") or "").strip()
        meta = " · ".join(value for value in (source, published) if value)
        lines.append(f"\n{index}. {title}\n   {meta}")
        if url:
            lines.append(f"   {url}")
    lines.append("\n下一步可回复：用第 3 条给蓝血经营管理系统和蓝血家族办公室改写")
    return "\n".join(lines)


def format_status(batch: dict[str, Any]) -> str:
    progress = batch.get("progress") or {}
    lines = [
        f'批次 {batch["id"]}：{batch.get("status")}',
        f'完成 {progress.get("completed", 0)}/{progress.get("total", 0)}',
    ]
    for job in batch["jobs"]:
        line = f'任务 #{job["id"]} · {job["account_name"]} · {job["status"]}'
        failure = dict(job.get("failure") or {})
        if failure:
            line += f'\n  {failure.get("title") or "处理失败"}：{failure.get("reason") or ""}'
            recommendation = str(failure.get("recommendation") or "").strip()
            if recommendation:
                line += f"\n  建议：{recommendation}"
        lines.append(line)
    if batch.get("status") == "ready_for_review":
        lines.append("\n下一步可回复：预览蓝血家族办公室的文章")
    elif batch.get("status") == "ready_for_draft":
        lines.append("\n全部文章已确认。下一步可回复：确认全部写入草稿箱")
    return "\n".join(lines)


def format_article_preview(job: dict[str, Any]) -> str:
    body = str(job.get("body") or "（正文尚未生成）")
    title_lines = "\n".join(
        f"标题{index}：{value}"
        for index, value in enumerate(job.get("titles") or [], 1)
    )
    subtitle_lines = "\n".join(
        f"副标题{index}：{value}"
        for index, value in enumerate(job.get("subtitles") or [], 1)
    )
    review_link = str(job.get("review_url") or "").strip()
    link_line = f"\n桌面审核工作台：{review_link}\n" if review_link else ""
    return (
        f'【{job["account_name"]}】任务 #{job["id"]} 正文预览：\n\n'
        f'{body[:7000]}\n\n{title_lines}\n{subtitle_lines}\n\n'
        f"{link_line}"
        f'下一步可回复：任务 {job["id"]} 使用标题 2、副标题 1'
    )


def format_review(batch: dict[str, Any]) -> str:
    jobs = list(batch.get("jobs") or [])
    accounts = "、".join(str(job.get("account_name") or "") for job in jobs)
    return (
        f'批次 {batch["id"]} 已生成完成，共 {len(jobs)} 篇：{accounts}\n'
        "现在进入逐篇审核。机器人会先发送第 1 篇预览；选择标题后仍需"
        "明确确认文章，确认后才会发送下一个公众号的文章。全部文章确认后"
        "才能写入草稿箱。"
    )


def format_draft_result(batch: dict[str, Any]) -> str:
    lines = [f'批次 {batch["id"]} 写入完成：']
    for job in batch["jobs"]:
        detail = (
            "已进入草稿箱"
            if job["status"] == "drafted"
            else _failure_summary(job)
        )
        lines.append(f'{job["account_name"]}：{detail}')
    return "\n".join(lines)


def _failure_summary(job: dict[str, Any]) -> str:
    failure = dict(job.get("failure") or {})
    if not failure:
        return f'失败：{job.get("error") or job.get("status") or "未知原因"}'
    title = str(failure.get("title") or "处理失败")
    reason = str(failure.get("reason") or "").strip()
    recommendation = str(failure.get("recommendation") or "").strip()
    parts = [title]
    if reason:
        parts.append(reason)
    if recommendation:
        parts.append(f"建议：{recommendation}")
    return "；".join(parts)
