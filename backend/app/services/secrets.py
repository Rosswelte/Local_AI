import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretService:
    """Encrypts provider credentials; the master key never lives in SQLite."""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "secrets" / "master.key"
        raw = os.getenv("ORCHESTRATOR_SECRET_KEY")
        if raw:
            key = raw.encode()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                key = self.path.read_bytes().strip()
                os.chmod(self.path, 0o600)
            else:
                key = Fernet.generate_key()
                self.path.write_bytes(key + b"\n")
                os.chmod(self.path, 0o600)
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ValueError("secret cannot be decrypted") from exc
