import asyncio
from pathlib import Path

import pytest

from app.database.database import Database
from app.orchestrator.jobs import JobManager
from app.orchestrator.resource_manager import ResourceManager
from app.providers.fake import FakeProvider


MIGRATIONS = Path(__file__).parents[1] / "app" / "database" / "migrations"


async def wait_for_state(db, job_id, states):
    for _ in range(100):
        state = await db.read(lambda con, jid: con.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0], job_id)
        if state in states:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("job did not reach a terminal state")


async def seed_job(db, provider="fake"):
    def seed(con):
        con.execute("INSERT INTO models(name, label, provider, ram_min_mb, installed) VALUES ('fake:model', 'Fake', ?, 10, 1)", (provider,))
        con.execute("INSERT INTO conversations(title, model_id) VALUES ('test', 1)")
        con.execute("INSERT INTO messages(conversation_id, role, content) VALUES (1, 'user', 'bonjour')")
        assistant = con.execute("INSERT INTO messages(conversation_id, role, content, status) VALUES (1, 'assistant', '', 'streaming')").lastrowid
        return con.execute("INSERT INTO jobs(provider, model_id, message_id) VALUES (?, 1, ?)", (provider, assistant)).lastrowid
    return await db.write(seed)


@pytest.mark.asyncio
async def test_fake_job_persists_message_and_completes(tmp_path):
    db = Database(tmp_path / "orchestrator.db")
    await db.start(MIGRATIONS)
    job_id = await seed_job(db)
    provider = FakeProvider(text="un deux", delay=0.001, memory={"ram_mb": 10, "vram_mb": 0})
    manager = JobManager(db, {"fake": provider}, ResourceManager({"ram_mb": 100, "vram_mb": 0}))
    await manager.start()
    await manager.enqueue()
    assert await wait_for_state(db, job_id, {"completed"}) == "completed"
    message = await db.read(lambda con: dict(con.execute("SELECT content, status FROM messages WHERE id=2").fetchone()))
    assert message == {"content": "un deux", "status": "complete"}
    await manager.stop()
    await db.close()


@pytest.mark.asyncio
async def test_running_job_cancellation_preserves_partial_text(tmp_path):
    db = Database(tmp_path / "orchestrator.db")
    await db.start(MIGRATIONS)
    job_id = await seed_job(db)
    provider = FakeProvider(text="un deux trois quatre", delay=0.03, memory={"ram_mb": 10, "vram_mb": 0})
    manager = JobManager(db, {"fake": provider}, ResourceManager({"ram_mb": 100, "vram_mb": 0}))
    await manager.start()
    await manager.enqueue()
    await wait_for_state(db, job_id, {"running"})
    await asyncio.sleep(0.05)
    await manager.cancel(job_id)
    assert await wait_for_state(db, job_id, {"cancelled"}) == "cancelled"
    message = await db.read(lambda con: dict(con.execute("SELECT content, status FROM messages WHERE id=2").fetchone()))
    assert message["status"] == "cancelled"
    assert message["content"]
    await manager.stop()
    await db.close()
