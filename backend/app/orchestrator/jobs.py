import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.errors import AppError
from app.orchestrator.events import EventHub
from app.orchestrator.resource_manager import ResourceManager
from app.providers.base import AIProvider


def _row(row):
    return dict(row) if row else None


def create_job(con, *, kind="text", provider="ollama", model_id=None, message_id=None, priority=10, force=False):
    cursor = con.execute("INSERT INTO jobs(kind, provider, model_id, message_id, priority, force) VALUES (?, ?, ?, ?, ?, ?)", (kind, provider, model_id, message_id, priority, int(force)))
    return cursor.lastrowid


def take_next_job(con):
    row = con.execute("SELECT * FROM jobs WHERE state = 'queued' ORDER BY priority DESC, created_at ASC, id ASC LIMIT 1").fetchone()
    if not row:
        return None
    con.execute("UPDATE jobs SET state='running', started_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
    return _row(con.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())


def restart_jobs(con):
    con.execute("""UPDATE messages SET status='error', updated_at=CURRENT_TIMESTAMP
        WHERE id IN (SELECT message_id FROM jobs WHERE state='running' AND kind IN ('text', 'agent') AND message_id IS NOT NULL)""")
    con.execute("UPDATE jobs SET state='failed', error_code='interrupted', error_message='Processus interrompu', finished_at=CURRENT_TIMESTAMP WHERE state='running' AND kind IN ('text', 'agent')")
    con.execute("UPDATE jobs SET state='queued' WHERE state='running' AND kind NOT IN ('text', 'agent')")


class JobManager:
    def __init__(self, db, providers: dict[str, AIProvider], resource_manager: ResourceManager):
        self.db = db
        self.providers = providers
        self.resources = resource_manager
        self.events = EventHub()
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._event_ids: dict[int, int] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._worker(), name="job-worker")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task:
            await self._task

    async def enqueue(self) -> None:
        self._wake.set()

    async def cancel(self, job_id: int) -> None:
        def update(con):
            row = con.execute("SELECT state, provider FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise AppError("not_found", "Job introuvable", 404)
            if row["state"] == "queued":
                con.execute("UPDATE jobs SET state='cancelled', finished_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
                return "queued"
            elif row["state"] == "running":
                con.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
                return "running", row["provider"]
            return row["state"], None

        result = await self.db.write(update)
        if result == "queued":
            await self._emit(job_id, "cancelled", {})
            await self.events.finish(job_id)
        elif isinstance(result, tuple) and result[0] == "running":
            await self.resources.cancel(job_id)
            await self.providers[result[1]].cancel(job_id)
        self._wake.set()

    async def _worker(self) -> None:
        while not self._stop.is_set():
            job = await self.db.write(take_next_job)
            if not job:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), 0.5)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                await self._run_job(job)
            except Exception as exc:
                if getattr(exc, "code", None) == "cancelled":
                    await self.db.write(lambda con: con.execute("UPDATE jobs SET state='cancelled', finished_at=CURRENT_TIMESTAMP WHERE id=?", (job["id"],)))
                    await self._set_message(job, job.get("_partial_text", ""), "cancelled")
                    await self._emit(job["id"], "cancelled", {})
                else:
                    await self.db.write(lambda con: con.execute("UPDATE jobs SET state='failed', error_code=?, error_message=?, finished_at=CURRENT_TIMESTAMP WHERE id=?", (getattr(exc, "code", "provider_error"), str(exc), job["id"])))
                    await self._set_message(job, job.get("_partial_text", ""), "error")
                    await self._emit(job["id"], "error", {"message": str(exc)})
            finally:
                await self.events.finish(job["id"])

    async def _run_job(self, job: dict[str, Any]) -> None:
        provider = self.providers[job["provider"]]
        model = await self.db.read(lambda con, model_id: _row(con.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()), job["model_id"])
        messages = await self.db.read(lambda con, message_id: _messages_for_job(con, message_id), job["message_id"])
        if not model:
            raise AppError("not_found", "Modèle introuvable", 404)
        estimate = provider.estimate(model)
        job["estimate"] = estimate
        reservation = await self.resources.acquire(job)
        text = ""
        try:
            stream = provider.pull(model["name"]) if job["kind"] == "pull" and hasattr(provider, "pull") else provider.run(model, messages, {"job_id": job["id"]})
            async for event in stream:
                cancelled = await self.db.read(lambda con, job_id: bool(con.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()[0]), job["id"])
                if cancelled:
                    await provider.cancel(job["id"])
                    await self.db.write(lambda con: con.execute("UPDATE jobs SET state='cancelled', finished_at=CURRENT_TIMESTAMP WHERE id=?", (job["id"],)))
                    await self._set_message(job, text, "cancelled")
                    return
                if event.type == "token":
                    text += str(event.data)
                    job["_partial_text"] = text
                    await self._append_message(job, text)
                elif event.type == "progress":
                    progress = _progress_value(event.data)
                    await self.db.write(lambda con, p: con.execute("UPDATE jobs SET progress=? WHERE id=?", (p, job["id"])), progress)
                await self._emit(job["id"], event.type, event.data)
            cancelled = await self.db.read(lambda con, job_id: bool(con.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()[0]), job["id"])
            if cancelled:
                await self.db.write(lambda con: con.execute("UPDATE jobs SET state='cancelled', finished_at=CURRENT_TIMESTAMP WHERE id=?", (job["id"],)))
                await self._set_message(job, text, "cancelled")
                await self._emit(job["id"], "cancelled", {})
                return
            await self._set_message(job, text, "complete")
            await self.db.write(lambda con: con.execute("UPDATE jobs SET state='completed', progress=100, finished_at=CURRENT_TIMESTAMP WHERE id=?", (job["id"],)))
            if job["kind"] == "pull":
                await self.db.write(lambda con: con.execute("UPDATE models SET installed=1 WHERE id=?", (job["model_id"],)))
        finally:
            await self.resources.release(reservation)

    async def _emit(self, job_id: int, event_type: str, data: Any) -> None:
        event_id = self._event_ids.get(job_id, 0) + 1
        self._event_ids[job_id] = event_id
        await self.events.publish(job_id, {"id": event_id, "type": event_type, "data": data})

    async def _append_message(self, job, text):
        if job.get("message_id"):
            await self.db.write(lambda con, content: con.execute("UPDATE messages SET content=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (content, job["message_id"])), text)

    async def _set_message(self, job, text, status):
        if job.get("message_id"):
            await self.db.write(lambda con, content, state: con.execute("UPDATE messages SET content=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (content, state, job["message_id"])), text, status)


def _messages_for_job(con, message_id):
    if not message_id:
        return []
    row = con.execute("SELECT conversation_id FROM messages WHERE id=?", (message_id,)).fetchone()
    if not row:
        return []
    return [dict(item) for item in con.execute("SELECT role, content FROM messages WHERE conversation_id=? AND id < ? ORDER BY id", (row["conversation_id"], message_id)).fetchall()]


def _progress_value(data: Any) -> int:
    if isinstance(data, int):
        return max(0, min(100, data))
    if isinstance(data, dict) and data.get("total"):
        return max(0, min(100, int(int(data.get("completed", 0)) * 100 / int(data["total"]))))
    return 0
