import asyncio
import sqlite3

import pytest

from app.database.database import Database, migrate, open_connection


def migration_dir():
    from pathlib import Path
    return Path(__file__).parents[1] / "app" / "database" / "migrations"


def test_migration_creates_v1_and_seven_tables(tmp_path):
    con = open_connection(tmp_path / "test.db")
    assert migrate(con, migration_dir()) == 3
    assert con.execute("PRAGMA user_version").fetchone()[0] == 3
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"machine_profile", "models", "conversations", "messages", "jobs", "job_events", "settings", "services", "behaviors"} <= tables
    assert migrate(con, migration_dir()) == 3


def test_foreign_keys_and_checks(tmp_path):
    con = open_connection(tmp_path / "test.db")
    migrate(con, migration_dir())
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO messages(conversation_id, role, content) VALUES (999, 'user', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO jobs(progress) VALUES (500)")
    con.execute("INSERT INTO conversations DEFAULT VALUES")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO messages(conversation_id, role, status) VALUES (1, 'assistant', 'fini')")
    con.execute("SELECT 1")


@pytest.mark.asyncio
async def test_parallel_writes_are_serialized(tmp_path):
    db = Database(tmp_path / "nested" / "test.db")
    await db.start(migration_dir())
    await asyncio.gather(*(db.write(lambda con, n: con.execute("INSERT INTO conversations(title) VALUES (?)", (str(n),)), n) for n in range(20)))
    count = await db.read(lambda con: con.execute("SELECT count(*) FROM conversations").fetchone()[0])
    assert count == 20
    await db.close()
