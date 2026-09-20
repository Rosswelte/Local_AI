from typing import Any


class ServiceManager:
    def __init__(self, db, providers: dict[str, Any]):
        self.db = db
        self.providers = providers

    async def ensure_local_services(self, ollama_url: str) -> None:
        await self.db.write(lambda con: con.execute("""INSERT INTO services(name, type, url, is_remote, last_status)
            VALUES ('ollama', 'llm', ?, 0, 'offline')
            ON CONFLICT(name) DO UPDATE SET type=excluded.type, url=excluded.url""", (ollama_url,)))

    async def check(self) -> list[dict[str, Any]]:
        rows = await self.db.read(lambda con: [dict(row) for row in con.execute("SELECT * FROM services ORDER BY id")])
        result = []
        for row in rows:
            provider = self.providers.get(row["name"])
            online = False
            if row["enabled"] and provider:
                try:
                    online = bool(await provider.health())
                except Exception:
                    online = False
            status = "online" if online else "offline"
            await self.db.write(lambda con, service_id, value: con.execute("UPDATE services SET last_status=?, last_check_at=CURRENT_TIMESTAMP WHERE id=?", (value, service_id)), row["id"], status)
            row["last_status"] = status
            result.append({key: value for key, value in row.items() if key not in {"api_key_enc", "extra_headers_enc"}})
        return result
