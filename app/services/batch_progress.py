from __future__ import annotations

import time
from collections.abc import Callable
from threading import Thread
from typing import Any


def batch_progress_signature(batch: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(batch.get("status") or ""),
        tuple(
            (
                int(job.get("id") or 0),
                str(job.get("status") or ""),
                str(job.get("step") or ""),
            )
            for job in batch.get("jobs") or []
        ),
    )


class BatchProgressMonitor:
    """Observe worker-backed batch state and emit only meaningful changes."""

    def __init__(
        self,
        load_batch: Callable[[str], dict[str, Any]],
        notify: Callable[[dict[str, Any]], None],
        *,
        interval_seconds: float = 1.0,
    ) -> None:
        self.load_batch = load_batch
        self.notify = notify
        self.interval_seconds = max(0.1, float(interval_seconds))

    def watch(self, batch_id: str, workers: list[Thread]) -> None:
        last_signature: tuple[Any, ...] | None = None
        while any(worker.is_alive() for worker in workers):
            last_signature = self._emit_if_changed(batch_id, last_signature)
            time.sleep(self.interval_seconds)
        self._emit_if_changed(batch_id, last_signature)

    def _emit_if_changed(
        self,
        batch_id: str,
        last_signature: tuple[Any, ...] | None,
    ) -> tuple[Any, ...]:
        batch = self.load_batch(batch_id)
        signature = batch_progress_signature(batch)
        if signature != last_signature:
            self.notify(batch)
        return signature
