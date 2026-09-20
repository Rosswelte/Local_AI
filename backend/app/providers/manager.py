from typing import Any


class ProviderManager:
    def __init__(self, providers: dict[str, Any] | None = None):
        self.providers = providers or {}

    def register(self, name: str, provider: Any) -> None:
        self.providers[name] = provider

    def get(self, name: str):
        return self.providers[name]

    async def health(self) -> dict[str, bool]:
        result = {}
        for name, provider in self.providers.items():
            try:
                result[name] = bool(await provider.health())
            except Exception:
                result[name] = False
        return result
