import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable


def open_connection(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def migrate(con: sqlite3.Connection, migrations_dir: str | Path) -> int:
    current = con.execute("PRAGMA user_version").fetchone()[0]
    for file in sorted(Path(migrations_dir).glob("[0-9][0-9][0-9]_*.sql")):
        number = int(file.name[:3])
        if number <= current:
            continue
        script = file.read_text(encoding="utf-8")
        try:
            con.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        current = number
    return current


class Database:
    """One SQLite writer connection confined to one dedicated thread."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._writer_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-writer")
        self._writer: sqlite3.Connection | None = None

    async def start(self, migrations_dir: str | Path) -> int:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

        def init() -> int:
            self._writer = open_connection(self.path)
            self._writer.execute("PRAGMA journal_mode = WAL")
            return migrate(self._writer, migrations_dir)

        return await asyncio.get_running_loop().run_in_executor(self._writer_pool, init)

    async def write(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        def run() -> Any:
            if self._writer is None:
                raise RuntimeError("database is not started")
            self._writer.execute("BEGIN IMMEDIATE")
            try:
                result = fn(self._writer, *args, **kwargs)
                self._writer.execute("COMMIT")
                return result
            except Exception:
                self._writer.execute("ROLLBACK")
                raise

        return await asyncio.get_running_loop().run_in_executor(self._writer_pool, run)

    async def read(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        def run() -> Any:
            con = open_connection(self.path)
            try:
                return fn(con, *args, **kwargs)
            finally:
                con.close()

        return await asyncio.to_thread(run)

    async def close(self) -> None:
        def close_writer() -> None:
            if self._writer is not None:
                self._writer.close()
                self._writer = None

        await asyncio.get_running_loop().run_in_executor(self._writer_pool, close_writer)
        self._writer_pool.shutdown(wait=True)
