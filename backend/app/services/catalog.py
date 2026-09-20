import json
from pathlib import Path
from typing import Any

import yaml


def load_catalog(path: str | Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    return data if isinstance(data, list) else data.get("models", [])


def upsert_catalog(con, entries: list[dict[str, Any]]) -> None:
    for item in entries:
        con.execute(
            """INSERT INTO models(name, label, display_name, provider, type, capabilities, size_mb, ram_min_mb,
               ram_recommended_mb, vram_min_mb, vram_recommended_mb, disk_mb, gpu_required, context_tokens, spec, perf)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET label=excluded.label, display_name=excluded.display_name,
               provider=excluded.provider, type=excluded.type, capabilities=excluded.capabilities,
               size_mb=excluded.size_mb, ram_min_mb=excluded.ram_min_mb, vram_min_mb=excluded.vram_min_mb,
               ram_recommended_mb=excluded.ram_recommended_mb, vram_recommended_mb=excluded.vram_recommended_mb,
               disk_mb=excluded.disk_mb, gpu_required=excluded.gpu_required, context_tokens=excluded.context_tokens,
               spec=excluded.spec, updated_at=CURRENT_TIMESTAMP""",
            (
                item["name"], item.get("label", item["name"]), item.get("display_name", item.get("label", item["name"])),
                item.get("provider", "ollama"), item.get("type", "text"), json.dumps(item.get("capabilities", [])),
                item.get("size_mb", 0), item.get("ram_min_mb", 0), item.get("ram_recommended_mb"),
                item.get("vram_min_mb", 0), item.get("vram_recommended_mb"), item.get("disk_mb", item.get("size_mb", 0)),
                int(bool(item.get("gpu_required", False))), item.get("context_tokens", 4096),
                json.dumps(item.get("spec", {})), json.dumps(item.get("perf", {})),
            ),
        )
