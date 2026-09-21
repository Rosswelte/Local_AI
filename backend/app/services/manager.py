import json
from typing import Any

from app.providers.openai_compatible import OpenAICompatibleProvider
from app.services.secrets import SecretService


class ServiceManager:
    def __init__(self, db, providers: dict[str, Any], secrets: SecretService):
        self.db = db
        self.providers = providers
        self.secrets = secrets

    @staticmethod
    def provider_key(service_id: int) -> str:
        return f"service:{service_id}"

    async def ensure_local_services(self, ollama_url: str) -> None:
        await self.db.write(lambda con: con.execute("""INSERT INTO services(name, type, url, is_remote, last_status)
            VALUES ('ollama', 'llm', ?, 0, 'offline')
            ON CONFLICT(name) DO UPDATE SET type=excluded.type, url=excluded.url""", (ollama_url,)))

    async def register_remote_services(self) -> None:
        rows = await self.db.read(lambda con: [dict(row) for row in con.execute("SELECT * FROM services WHERE is_remote=1 AND enabled=1")])
        for row in rows:
            await self.register_remote_service(row)

    async def register_remote_service(self, row: dict[str, Any]) -> OpenAICompatibleProvider:
        if not row.get("api_key_enc"):
            raise ValueError("remote service has no API key")
        headers = {}
        if row.get("extra_headers_enc"):
            headers = json.loads(self.secrets.decrypt(row["extra_headers_enc"]))
        provider = OpenAICompatibleProvider(row["url"], self.secrets.decrypt(row["api_key_enc"]), row.get("name", ""), headers)
        self.providers[self.provider_key(int(row["id"]))] = provider
        return provider

    async def close_remote(self) -> None:
        for name, provider in list(self.providers.items()):
            if name.startswith("service:") and hasattr(provider, "close"):
                await provider.close()

    async def check(self) -> list[dict[str, Any]]:
        rows = await self.db.read(lambda con: [dict(row) for row in con.execute("SELECT * FROM services ORDER BY id")])
        result = []
        for row in rows:
            provider_name = row["name"] if not row["is_remote"] else self.provider_key(int(row["id"]))
            provider = self.providers.get(provider_name)
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
