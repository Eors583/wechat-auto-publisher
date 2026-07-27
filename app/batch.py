from __future__ import annotations

import asyncio
import threading
from typing import Any


async def run_pipelines_concurrently(
    task_items: list[dict[str, Any]], *, review: bool,
    cancel_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """Run one blocking Pipeline per account in independent worker threads."""

    async def run_one(item: dict[str, Any]) -> dict[str, Any]:
        pipe = item["pipe"]
        job_id = int(item["job_id"])

        def execute() -> dict[str, Any]:
            try:
                return pipe.run_job(
                    job_id,
                    review=review,
                    cover_media_id=None,
                    selected_title_index=None,
                    from_step="ingest",
                )
            except Exception:  # noqa: BLE001
                return pipe.db.get_job(job_id) or {"id": job_id, "status": "failed"}

        return await asyncio.to_thread(execute)

    # Each account owns a separately scheduled task. gather preserves the
    # selected-account order while all worker threads run at once.
    workers = [
        asyncio.create_task(
            run_one(item), name=f'official-account-job-{item["job_id"]}'
        )
        for item in task_items
    ]
    if cancel_event is None:
        return list(await asyncio.gather(*workers))
    while not all(worker.done() for worker in workers):
        if cancel_event.is_set():
            for item in task_items:
                pipe = item["pipe"]
                if hasattr(pipe.db, "update_job"):
                    pipe.db.update_job(
                        int(item["job_id"]),
                        status="cancelled",
                        error="用户已终止改写",
                    )
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            return [
                item["pipe"].db.get_job(int(item["job_id"]))
                or {"id": int(item["job_id"]), "status": "cancelled"}
                for item in task_items
            ]
        await asyncio.sleep(0.1)
    return list(await asyncio.gather(*workers))


async def inject_pipelines_concurrently(
    task_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Write all reviewed account variants to their own draft boxes concurrently."""

    async def inject_one(item: dict[str, Any]) -> dict[str, Any]:
        pipe = item["pipe"]
        job_id = int(item["job_id"])
        raw_title_index = item.get("title_index")
        title_index = (
            int(raw_title_index) if raw_title_index is not None else None
        )

        def execute() -> dict[str, Any]:
            try:
                return pipe.review_and_inject(job_id, title_index=title_index)
            except Exception:  # noqa: BLE001
                return pipe.db.get_job(job_id) or {"id": job_id, "status": "failed"}

        return await asyncio.to_thread(execute)

    workers = [
        asyncio.create_task(
            inject_one(item), name=f'official-account-inject-{item["job_id"]}'
        )
        for item in task_items
    ]
    return list(await asyncio.gather(*workers))
