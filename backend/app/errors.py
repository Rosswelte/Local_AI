from typing import Any


class AppError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}
