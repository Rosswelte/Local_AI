import pytest

from app.database.database import Database
from app.orchestrator.jobs import restart_jobs


@pytest.mark.asyncio
async def test_restart_marks_running_text_and_message_as_interrupted(tmp_path):
    migrations = __import__("pathlib").Path(__file__).parents[1] / "app" / "database" / "migrations"
    db = Database(tmp_path / "orchestrator.db")
    await db.start(migrations)

    def seed(con):
        con.execute("INSERT INTO conversations(title) VALUES ('test')")
        con.execute("INSERT INTO messages(conversation_id, role, content, status) VALUES (1, 'assistant', 'partiel', 'streaming')")
        con.execute("INSERT INTO jobs(kind, message_id, state) VALUES ('text', 1, 'running')")

    await db.write(seed)
    await db.write(restart_jobs)
    job, message = await db.read(lambda con: (dict(con.execute("SELECT state, error_code FROM jobs WHERE id=1").fetchone()), dict(con.execute("SELECT status, content FROM messages WHERE id=1").fetchone())))
    assert job == {"state": "failed", "error_code": "interrupted"}
    assert message == {"status": "error", "content": "partiel"}
    await db.close()
