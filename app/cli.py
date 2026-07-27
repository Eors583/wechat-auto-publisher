from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from app.ai import TITLE_CANDIDATE_COUNT, clean_candidate_list
from app.config import load_config
from app.db import Database
from app.pipeline import Pipeline
from app.providers.topic import from_keyword_file, from_manual
from app.wechat.publish import schedule_publish

app = typer.Typer(add_completion=False, no_args_is_help=True, help="微信公众号自动改写与草稿发布")
console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


def _pipeline(config_path: Optional[str] = None) -> Pipeline:
    cfg = load_config(config_path)
    return Pipeline(cfg)


@app.command()
def run(
    url: Optional[str] = typer.Option(None, help="原文链接"),
    text: Optional[str] = typer.Option(None, help="纯文本内容"),
    text_file: Optional[Path] = typer.Option(None, "--text-file", help="纯文本文件路径"),
    topic: Optional[str] = typer.Option(None, help="话题（手动）"),
    keyword_file: Optional[Path] = typer.Option(None, help="关键词文件，每行一个"),
    keyword_index: int = typer.Option(0, help="关键词文件行号（从 0 开始）"),
    mode: str = typer.Option("draft", help="draft | publish"),
    review: bool = typer.Option(False, help="生成后暂停，待 review 再注入草稿"),
    cover_media_id: Optional[str] = typer.Option(None, help="强制指定首图 media_id"),
    title_index: Optional[int] = typer.Option(None, help="自动选用第 N 个候选标题（0-based）"),
    config: Optional[str] = typer.Option(None, "--config", help="配置文件路径"),
) -> None:
    """运行完整流水线：摄入 → 重写 → 标题 → 渲染 → 草稿/发布。"""
    if text_file:
        text = text_file.read_text(encoding="utf-8")
    if not url and not text:
        raise typer.BadParameter("请提供 --url / --text / --text-file 之一")

    source = "manual"
    topic_value = topic
    if keyword_file:
        t = from_keyword_file(str(keyword_file), keyword_index)
        topic_value = topic_value or t.topic
        source = t.source
    elif topic:
        topic_value = from_manual(topic).topic

    pipe = _pipeline(config)
    with console.status("Running pipeline..."):
        job = pipe.create_and_run(
            topic=topic_value,
            url=url,
            text=text,
            source=source,
            mode=mode,
            review=review,
            cover_media_id=cover_media_id,
            selected_title_index=title_index,
        )
    _print_job(job)
    if job.get("status") == "ready_for_review":
        console.print(
            f"[yellow]已暂停待审核。执行:[/yellow] python -m app review --job-id {job['id']}"
        )


@app.command()
def retry(
    job_id: int = typer.Option(..., "--job-id", help="任务 ID"),
    from_step: str = typer.Option(
        "rewrite",
        "--from-step",
        help="ingest|rewrite|title_optimize|render|inject",
    ),
    review: bool = typer.Option(False, help="渲染后暂停审核"),
    cover_media_id: Optional[str] = typer.Option(None),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """从指定步骤断点续跑。"""
    pipe = _pipeline(config)
    job = pipe.run_job(
        job_id,
        review=review,
        cover_media_id=cover_media_id,
        from_step=from_step,
    )
    _print_job(job)


@app.command("list-jobs")
def list_jobs(
    limit: int = typer.Option(20, help="条数"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """列出最近任务。"""
    cfg = load_config(config)
    db = Database(cfg["_db_path"])
    jobs = db.list_jobs(limit=limit)
    table = Table(title="Jobs")
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("Step")
    table.add_column("Topic")
    table.add_column("Title")
    table.add_column("Draft")
    for job in jobs:
        table.add_row(
            str(job["id"]),
            str(job.get("status")),
            str(job.get("step")),
            (job.get("topic") or "")[:24],
            (job.get("selected_title") or "")[:28],
            (job.get("draft_media_id") or "")[:18],
        )
    console.print(table)


@app.command()
def review(
    job_id: int = typer.Option(..., "--job-id"),
    title_index: Optional[int] = typer.Option(None, help="直接指定候选标题序号"),
    cover_media_id: Optional[str] = typer.Option(None),
    publish_now: bool = typer.Option(False, help="审核后立即发布"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """交互选择标题后注入草稿箱。"""
    pipe = _pipeline(config)
    job = pipe.db.get_job(job_id)
    if not job:
        raise typer.BadParameter(f"Job not found: {job_id}")

    candidates = clean_candidate_list(
        list(job.get("title_candidates") or job.get("titles") or []),
        limit=TITLE_CANDIDATE_COUNT,
    )
    if not candidates:
        console.print("[red]没有候选标题，请先完成 rewrite / title_optimize[/red]")
        raise typer.Exit(1)

    console.print("[bold]候选标题：[/bold]")
    for i, title in enumerate(candidates):
        console.print(f"  [{i}] {title}")

    if title_index is None:
        title_index = typer.prompt("选择标题序号", type=int, default=0)

    job = pipe.review_and_inject(
        job_id,
        title_index=title_index,
        cover_media_id=cover_media_id,
        publish_now=publish_now,
    )
    _print_job(job)


@app.command()
def publish(
    job_id: int = typer.Option(..., "--job-id"),
    at: Optional[str] = typer.Option(None, help='定时时间，如 "2026-07-14 09:00"'),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """立即发布或本地定时发布。"""
    pipe = _pipeline(config)
    if at:
        run_at = _parse_local_dt(at)
        pipe.db.update_job(job_id, scheduled_at=run_at.isoformat(), mode="publish")

        def _cb(jid: int) -> None:
            try:
                Pipeline(load_config(config)).publish_job(jid)
            except Exception:  # noqa: BLE001
                logging.exception("Scheduled publish failed for job %s", jid)

        schedule_publish(run_at, job_id, _cb)
        console.print(f"[green]已安排定时发布[/green] job={job_id} at={run_at}")
        console.print("[yellow]请保持进程运行直到定时触发（或改用系统任务调用 publish）[/yellow]")
        # Keep process alive until job fires for short demos; for long waits prefer OS scheduler.
        import time

        while True:
            job = pipe.db.get_job(job_id)
            if not job or job.get("status") in {"published", "failed"}:
                break
            time.sleep(2)
        _print_job(pipe.db.get_job(job_id) or {"id": job_id})
        return

    job = pipe.publish_job(job_id)
    _print_job(job)


@app.command()
def show(
    job_id: int = typer.Option(..., "--job-id"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """查看单个任务详情。"""
    cfg = load_config(config)
    job = Database(cfg["_db_path"]).get_job(job_id)
    if not job:
        raise typer.BadParameter(f"Job not found: {job_id}")
    _print_job(job, verbose=True)


def _print_job(job: dict, verbose: bool = False) -> None:
    console.print(
        f"[bold]Job #{job.get('id')}[/bold] status={job.get('status')} step={job.get('step')}"
    )
    console.print(f"topic: {job.get('topic')}")
    console.print(f"title: {job.get('selected_title')}")
    console.print(f"draft_media_id: {job.get('draft_media_id')}")
    console.print(f"publish_id: {job.get('publish_id')}")
    if job.get("error"):
        console.print(f"[red]error: {job.get('error')}[/red]")
    if verbose:
        console.print(f"candidates: {job.get('title_candidates')}")
        console.print(f"thumb_media_id: {job.get('thumb_media_id')}")
        console.print(f"ad_id: {job.get('ad_id')}")
        body = job.get("body") or ""
        console.print(f"body_preview: {body[:200]}...")


def _parse_local_dt(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise typer.BadParameter(f"无法解析时间: {value}")


@app.command("desktop")
def desktop() -> None:
    """打开运营人员桌面端界面。"""
    from app.ui.desktop import main as ui_main

    ui_main()


if __name__ == "__main__":
    app()
