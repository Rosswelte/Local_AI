import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ollama_url: str
    expose_host: str
    admin_password: str
    log_level: str
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("DATA_DIR", "./data")).expanduser()
        expose_host = os.getenv("EXPOSE_HOST", "127.0.0.1")
        admin_password = os.getenv("ADMIN_PASSWORD", "")
        if expose_host != "127.0.0.1" and not admin_password:
            raise RuntimeError("ADMIN_PASSWORD is required when EXPOSE_HOST is not 127.0.0.1")
        return cls(
            data_dir=data_dir,
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            expose_host=expose_host,
            admin_password=admin_password,
            log_level=os.getenv("LOG_LEVEL", "info"),
            port=int(os.getenv("PORT", "8000")),
        )
