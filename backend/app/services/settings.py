import json
from typing import Any


DEFAULTS: dict[str, tuple[Any, str]] = {
    "ram_budget_mb": (None, "limits"),
    "vram_budget_mb": (None, "limits"),
    "allow_parallel": (None, "limits"),
    "idle_unload_minutes": (5, "limits"),
    "min_free_ram_percent": (None, "limits"),
    "remote_monthly_token_limit": (None, "limits"),
    "remote_warn_before_send": (True, "features"),
    "web_enabled": (False, "features"),
    "language": ("fr", "appearance"),
}


def read_settings(con) -> dict[str, dict[str, Any]]:
    stored = {row["key"]: {"value": json.loads(row["value"]) if row["value"] is not None else None, "category": row["category"]} for row in con.execute("SELECT key, value, category FROM settings")}
    result = {}
    for key, (value, category) in DEFAULTS.items():
        result[key] = stored.pop(key, {"value": value, "category": category})
    result.update(stored)
    return result


def write_settings(con, values: dict[str, Any]) -> None:
    for key, value in values.items():
        category = DEFAULTS.get(key, (None, "limits"))[1]
        encoded = None if value is None else json.dumps(value)
        con.execute("INSERT INTO settings(key, value, category) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, category=excluded.category", (key, encoded, category))
