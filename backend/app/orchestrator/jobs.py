import asyncio
import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.errors import AppError
from app.orchestrator.events import EventHub
from app.orchestrator.resource_manager import ResourceManager
from app.orchestrator.scheduler import Scheduler
from app.orchestrator.memory_guard import MemoryGuard
from app.providers.base import AIProvider
from app.services.image_outputs import save_output


def _row(row):
    return dict(row) if row else None


def create_job(con, *, kind="text", provider="ollama", model_id=None, message_id=None, conversation_id=None, priority=10, force=False, input_data=None):
    cursor = con.execute(
        "INSERT INTO jobs(kind, provider, model_id, message_id, conversation_id, priority, force, input) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, provider, model_id, message_id, conversation_id, priority, int(force), json.dumps(input_data or {})),
    )
    return cursor.lastrowid


def take_next_job(con):
    row = con.execute("SELECT * FROM jobs WHERE state = 'queued' AND (next_run_at IS NULL OR next_run_at <= CURRENT_TIMESTAMP) ORDER BY priority DESC, created_at ASC, id ASC LIMIT 1").fetchone()
    if not row:
        return None
    con.execute("UPDATE jobs SET state='running', attempt=attempt + 1, started_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
    return _row(con.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())


def restart_jobs(con):
    con.execute("""UPDATE messages SET status='error', updated_at=CURRENT_TIMESTAMP
        WHERE id IN (SELECT message_id FROM jobs WHERE state='running' AND kind IN ('text', 'agent') AND message_id IS NOT NULL)""")
    con.execute("UPDATE jobs SET state='failed', error_code='interrupted', error_message='Processus interrompu', finished_at=CURRENT_TIMESTAMP WHERE state='running' AND kind IN ('text', 'agent')")
    con.execute("UPDATE jobs SET state='queued' WHERE state='running' AND kind NOT IN ('text', 'agent')")


class JobManager:
    def __init__(self, db, providers: dict[str, AIProvider], resource_manager: ResourceManager, scheduler: Scheduler | None = None, memory_guard_percent: float = 5.0, data_dir: str | Path = "."):
        self.db = db
        self.providers = providers
        self.resources = resource_manager
        self.scheduler = scheduler or Scheduler()
        self.memory_guard_percent = memory_guard_percent
        self.data_dir = Path(data_dir)
        self.events = EventHub()
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._event_ids: dict[int, int] = {}

    async def start(self, worker_count: int | None = None) -> None:
        count = worker_count or self.scheduler.worker_count
        self._tasks = [asyncio.create_task(self._worker(index), name=f"job-worker-{index}") for index in range(count)]

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._tasks:
            await asyncio.gather(*self._tasks)

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

    async def _worker(self, worker_id: int) -> None:
        while not self._stop.is_set():
            job = await self.db.write(take_next_job)
            if not job:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), 0.5)
                except asyncio.TimeoutError:
                    pass
                continue
            terminal = True
            try:
                policy = self.scheduler.policy(job)
                await asyncio.wait_for(self._run_job(job), timeout=policy.timeout_s)
            except asyncio.CancelledError:
                cancelled = await self.db.read(lambda con, job_id: bool(con.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()[0]), job["id"])
                if not cancelled:
                    raise
                terminal = not await self._handle_failure(job, AppError("cancelled", "Le job a été annulé", 409))
            except asyncio.TimeoutError:
                terminal = not await self._handle_failure(job, AppError("provider_timeout", "Le provider n'a pas répondu dans le délai", 504))
            except Exception as exc:
                terminal = not await self._handle_failure(job, exc)
            finally:
                if terminal:
                    await self.events.finish(job["id"])

    async def _handle_failure(self, job: dict[str, Any], exc: Exception) -> bool:
        code = getattr(exc, "code", "provider_error")
        policy = self.scheduler.policy(job)
        if self.scheduler.can_retry(code, bool(job.get("retryable", 1))) and int(job.get("attempt", 1)) <= policy.max_retries:
            delay = self.scheduler.retry_delay(int(job.get("attempt", 1)))
            await self.db.write(lambda con, retry_code, retry_message, retry_delay: con.execute("UPDATE jobs SET state='queued', error_code=?, error_message=?, next_run_at=datetime('now', ?), finished_at=NULL WHERE id=?", (retry_code, retry_message, f"+{retry_delay} seconds", job["id"])), code, str(exc), delay)
            await self._emit(job["id"], "warning", {"code": "retry_scheduled", "attempt": job.get("attempt", 1), "delay_s": delay})
            return True
        if code == "cancelled":
            await self.db.write(lambda con: con.execute("UPDATE jobs SET state='cancelled', finished_at=CURRENT_TIMESTAMP WHERE id=?", (job["id"],)))
            await self._set_message(job, job.get("_partial_text", ""), "cancelled")
            await self._emit(job["id"], "cancelled", {})
        else:
            await self.db.write(lambda con: con.execute("UPDATE jobs SET state='failed', error_code=?, error_message=?, finished_at=CURRENT_TIMESTAMP WHERE id=?", (code, str(exc), job["id"])))
            await self._set_message(job, job.get("_partial_text", ""), "error")
            await self._emit(job["id"], "error", {"code": code, "message": str(exc)})
        return False

    async def _run_job(self, job: dict[str, Any]) -> None:
        provider = self.providers[job["provider"]]
        if job["kind"] == "image":
            await self._run_image_job(job, provider)
            return
        model = await self.db.read(lambda con, model_id: _row(con.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()), job["model_id"])
        messages = await self.db.read(lambda con, message_id: _messages_for_job(con, message_id), job["message_id"])
        if not model:
            raise AppError("not_found", "Modèle introuvable", 404)
        estimate = provider.estimate(model)
        job["estimate"] = estimate
        job["model_name"] = model["name"]
        job["pinned"] = bool(model.get("pinned", 0))
        reservation = await self.resources.acquire(job)
        text = ""
        guard_stop = asyncio.Event()
        guard_task = None
        if job.get("memory_guard"):
            guard = MemoryGuard(self.memory_guard_percent)

            async def stop_for_memory():
                await self.db.write(lambda con: con.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job["id"],)))
                await self._emit(job["id"], "warning", {"code": "memory_guard", "message": "RAM libre sous le seuil; annulation du job"})
                await provider.cancel(job["id"])

            guard_task = asyncio.create_task(guard.watch(guard_stop, stop_for_memory), name=f"memory-guard-{job['id']}")
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
            if guard_task:
                guard_stop.set()
                guard_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await guard_task
            await self.resources.release(reservation)

    async def _run_image_job(self, job: dict[str, Any], provider) -> None:
        payload = json.loads(job.get("input") or "{}")
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict):
            raise AppError("invalid_input", "Workflow image invalide", 422)
        job["estimate"] = provider.estimate(payload)
        job["model_name"] = ""
        reservation = await self.resources.acquire(job)
        outputs = []
        try:
            async for event in provider.run_image(workflow, int(job["id"])):
                cancelled = await self.db.read(lambda con, job_id: bool(con.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()[0]), job["id"])
                if cancelled:
                    with contextlib.suppress(Exception):
                        await provider.cancel(job["id"])
                    await self.db.write(lambda con: con.execute("UPDATE jobs SET state='cancelled', finished_at=CURRENT_TIMESTAMP WHERE id=?", (job["id"],)))
                    await self._emit(job["id"], "cancelled", {})
                    return
                if event.type == "progress":
                    progress = _progress_value(event.data)
                    await self.db.write(lambda con, p: con.execute("UPDATE jobs SET progress=? WHERE id=?", (p, job["id"])), progress)
                    await self._emit(job["id"], event.type, event.data)
                elif event.type == "output":
                    content, media_type = await provider.download_output(event.data)
                    metadata = await asyncio.to_thread(save_output, self.data_dir, int(job["id"]), len(outputs) + 1, content, media_type)
                    outputs.append(metadata)
                    await self._emit(job["id"], "output", metadata)
                else:
                    await self._emit(job["id"], event.type, event.data)
            await self.db.write(
                lambda con, value: con.execute(
                    "UPDATE jobs SET output=?, state='completed', progress=100, finished_at=CURRENT_TIMESTAMP WHERE id=?",
                    (json.dumps(value), job["id"]),
                ),
                outputs,
            )
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
