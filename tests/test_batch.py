from __future__ import annotations

import asyncio
import threading
import time

from app.batch import inject_pipelines_concurrently, run_pipelines_concurrently


class _FakeDb:
    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        self.status = "failed"

    def get_job(self, job_id: int):
        return {"id": job_id, "status": self.status}

    def update_job(self, _job_id: int, **fields):
        self.status = fields.get("status", self.status)


class _FakePipeline:
    def __init__(self, job_id: int, tracker: dict, lock: threading.Lock) -> None:
        self.db = _FakeDb(job_id)
        self.tracker = tracker
        self.lock = lock

    def run_job(self, job_id: int, **_kwargs):
        with self.lock:
            self.tracker["active"] += 1
            self.tracker["max_active"] = max(
                self.tracker["max_active"], self.tracker["active"]
            )
        time.sleep(0.15)
        with self.lock:
            self.tracker["active"] -= 1
        return {"id": job_id, "status": "drafted"}

    def review_and_inject(self, job_id: int, **_kwargs):
        return self.run_job(job_id)


def test_account_pipelines_run_concurrently() -> None:
    tracker = {"active": 0, "max_active": 0}
    lock = threading.Lock()
    items = [
        {"pipe": _FakePipeline(i, tracker, lock), "job_id": i}
        for i in (1, 2, 3)
    ]

    started = time.monotonic()
    results = asyncio.run(run_pipelines_concurrently(items, review=False))
    elapsed = time.monotonic() - started

    assert tracker["max_active"] == 3
    assert elapsed < 0.35
    assert [result["id"] for result in results] == [1, 2, 3]
    assert all(result["status"] == "drafted" for result in results)


def test_account_pipelines_can_be_cancelled_before_draft_injection() -> None:
    tracker = {"active": 0, "max_active": 0}
    lock = threading.Lock()
    items = [
        {"pipe": _FakePipeline(i, tracker, lock), "job_id": i}
        for i in (11, 12)
    ]
    cancel_event = threading.Event()
    cancel_event.set()

    results = asyncio.run(
        run_pipelines_concurrently(
            items, review=False, cancel_event=cancel_event
        )
    )

    assert [result["status"] for result in results] == ["cancelled", "cancelled"]


def test_reviewed_accounts_are_injected_concurrently() -> None:
    tracker = {"active": 0, "max_active": 0}
    lock = threading.Lock()
    items = [
        {"pipe": _FakePipeline(i, tracker, lock), "job_id": i, "title_index": 0}
        for i in (21, 22, 23)
    ]
    started = time.monotonic()
    results = asyncio.run(inject_pipelines_concurrently(items))
    elapsed = time.monotonic() - started
    assert tracker["max_active"] == 3
    assert elapsed < 0.35
    assert all(item["status"] == "drafted" for item in results)
