from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import Database


REVIEW_QUEUE_STATUSES = frozenset(
    {"ready_for_review", "injecting", "drafted", "published"}
)


class FeishuSessionStore:
    def __init__(self, db: Database, *, integration_id: str = "") -> None:
        self.db = db
        self.integration_id = str(integration_id or "").strip()

    def current_batch_id(self, chat_id: str) -> str | None:
        if self.integration_id:
            return self.db.get_feishu_session(
                self.integration_id, chat_id
            ).get("batch_id")
        return self.db.get_bot_session(chat_id)

    def bind_batch(self, chat_id: str, batch_id: str) -> None:
        if self.integration_id:
            self.db.set_feishu_session(
                self.integration_id, chat_id, batch_id=batch_id
            )
        else:
            self.db.set_bot_session(chat_id, batch_id)
        self.update(chat_id, stage="generating", current_batch_id=batch_id)

    def get(self, chat_id: str) -> dict[str, Any]:
        if self.integration_id:
            return dict(
                self.db.get_feishu_session(
                    self.integration_id, chat_id
                ).get("context")
                or {}
            )
        return self.db.get_bot_context(chat_id)

    def update(self, chat_id: str, **fields: Any) -> dict[str, Any]:
        context = self.get(chat_id)
        context.update(fields)
        if self.integration_id:
            self.db.set_feishu_session(
                self.integration_id, chat_id, context=context
            )
        else:
            self.db.set_bot_context(chat_id, context)
        return context

    def save_hot_topics(self, chat_id: str, items: list[dict[str, Any]]) -> None:
        topics = [
            {
                "number": index,
                "title": str(item.get("title") or ""),
                "source": str(item.get("source") or ""),
                "url": str(item.get("url") or ""),
                "published_at": str(item.get("published_at") or ""),
            }
            for index, item in enumerate(items, 1)
        ]
        self.update(chat_id, recent_hot_topics=topics, stage="hot_topics_selected")

    def recent_hot_topics(self, chat_id: str) -> list[dict[str, Any]]:
        return list(self.get(chat_id).get("recent_hot_topics") or [])

    def hot_topic(self, chat_id: str, number: int) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.recent_hot_topics(chat_id)
                if _as_int(item.get("number")) == number
            ),
            None,
        )

    def start_review(self, chat_id: str, batch: dict[str, Any]) -> dict[str, Any] | None:
        return self.sync_review(chat_id, batch, reset_cursor=True)

    def sync_review(
        self,
        chat_id: str,
        batch: dict[str, Any],
        *,
        reset_cursor: bool = False,
    ) -> dict[str, Any] | None:
        """Synchronize chat review progress with the shared batch state.

        Desktop, API and Feishu can all edit the same batch.  Database review
        state is therefore authoritative; the session only keeps navigation
        state for the conversation.
        """

        queue = [
            {"job_id": int(job["id"]), "account_name": str(job["account_name"])}
            for job in batch.get("jobs") or []
            if str(job.get("status") or "") in REVIEW_QUEUE_STATUSES
        ]
        reviewed = {
            int(job["id"])
            for job in batch.get("jobs") or []
            if str(job.get("review_status") or "") == "confirmed"
            or str(job.get("status") or "") in {"drafted", "published"}
        }
        pending = [
            item
            for item in queue
            if _as_int(item.get("job_id")) not in reviewed
        ]
        current = self.current_review_job_id(chat_id)
        pending_ids = {
            _as_int(item.get("job_id"))
            for item in pending
        }
        if reset_cursor or current not in pending_ids:
            current = _as_int(pending[0].get("job_id")) if pending else None
        self.update(
            chat_id,
            stage="review_complete" if queue and not pending else "reviewing",
            review_queue=queue,
            reviewed_job_ids=sorted(reviewed),
            current_review_job_id=current,
        )
        return next(
            (
                item
                for item in pending
                if _as_int(item.get("job_id")) == current
            ),
            None,
        )

    def review_state(self, chat_id: str) -> dict[str, Any]:
        context = self.get(chat_id)
        queue = list(context.get("review_queue") or [])
        reviewed = {
            int(item)
            for item in context.get("reviewed_job_ids") or []
            if _as_int(item) is not None
        }
        return {
            "queue": queue,
            "reviewed_job_ids": sorted(reviewed),
            "current_review_job_id": _as_int(context.get("current_review_job_id")),
            "completed": len(reviewed),
            "total": len(queue),
        }

    def current_review_job_id(self, chat_id: str) -> int | None:
        return self.review_state(chat_id)["current_review_job_id"]

    def set_current_review_job(self, chat_id: str, job_id: int) -> None:
        self.update(chat_id, stage="reviewing", current_review_job_id=int(job_id))

    def mark_reviewed(self, chat_id: str, job_id: int) -> dict[str, Any]:
        state = self.review_state(chat_id)
        reviewed = set(state["reviewed_job_ids"])
        reviewed.add(int(job_id))
        next_item = next(
            (
                item
                for item in state["queue"]
                if _as_int(item.get("job_id")) not in reviewed
            ),
            None,
        )
        self.update(
            chat_id,
            stage="review_complete" if not next_item else "reviewing",
            reviewed_job_ids=sorted(reviewed),
            current_review_job_id=(
                _as_int(next_item.get("job_id")) if next_item else None
            ),
        )
        return {
            **self.review_state(chat_id),
            "next": next_item,
            "all_completed": next_item is None and bool(state["queue"]),
        }

    def reopen_review(
        self,
        chat_id: str,
        job_id: int,
        *,
        account_name: str = "",
    ) -> dict[str, Any]:
        """Return a changed article to the current Feishu review queue.

        Article edits invalidate an earlier confirmation in the shared
        database.  Keep the chat-local review cursor in sync so Feishu cannot
        continue to present the batch as fully reviewed after a revision.
        """

        state = self.review_state(chat_id)
        job_id = int(job_id)
        queue = list(state["queue"])
        if not any(_as_int(item.get("job_id")) == job_id for item in queue):
            queue.append(
                {
                    "job_id": job_id,
                    "account_name": str(account_name or ""),
                }
            )
        reviewed = set(state["reviewed_job_ids"])
        reviewed.discard(job_id)
        self.update(
            chat_id,
            stage="reviewing",
            review_queue=queue,
            reviewed_job_ids=sorted(reviewed),
            current_review_job_id=job_id,
        )
        return self.review_state(chat_id)

    def unreviewed_items(self, chat_id: str) -> list[dict[str, Any]]:
        state = self.review_state(chat_id)
        reviewed = set(state["reviewed_job_ids"])
        return [
            item
            for item in state["queue"]
            if _as_int(item.get("job_id")) not in reviewed
        ]

    def all_reviews_completed(self, chat_id: str) -> bool:
        state = self.review_state(chat_id)
        return state["total"] > 0 and state["completed"] >= state["total"]

    def set_pending_action(
        self,
        chat_id: str,
        *,
        tool: str,
        arguments: dict[str, Any],
        prompt: str,
        ttl_seconds: int = 300,
    ) -> dict[str, Any]:
        code = secrets.token_hex(3).upper()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=max(30, ttl_seconds))
        ).isoformat(timespec="seconds")
        pending = {
            "tool": str(tool),
            "arguments": dict(arguments),
            "prompt": str(prompt),
            "code": code,
            "expires_at": expires_at,
        }
        self.update(chat_id, pending_action=pending)
        return pending

    def pending_action(self, chat_id: str) -> dict[str, Any] | None:
        pending = self.get(chat_id).get("pending_action")
        if not isinstance(pending, dict) or not pending.get("tool"):
            return None
        try:
            expires_at = datetime.fromisoformat(str(pending.get("expires_at") or ""))
        except ValueError:
            self.clear_pending_action(chat_id)
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            self.clear_pending_action(chat_id)
            return None
        return dict(pending)

    def confirm_pending_action(
        self, chat_id: str, text: str
    ) -> dict[str, Any] | None:
        pending = self.pending_action(chat_id)
        if not pending:
            return None
        normalized = "".join(str(text or "").upper().split())
        code = str(pending.get("code") or "").upper()
        if "确认" not in normalized or code not in normalized:
            return None
        self.clear_pending_action(chat_id)
        return pending

    def clear_pending_action(self, chat_id: str) -> None:
        self.update(chat_id, pending_action=None)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
