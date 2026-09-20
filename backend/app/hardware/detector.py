import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil


@dataclass
class GPU:
    name: str
    total_mb: int
    free_mb: int


@dataclass
class MachineProfile:
    ram_total_mb: int
    ram_available_mb: int
    cpu_cores: int
    gpu: GPU | None
    container_ram_limit_mb: int | None
    ram_budget_mb: int
    vram_budget_mb: int

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["gpu"] = asdict(self.gpu) if self.gpu else None
        return data


def container_ram_limit_mb() -> int | None:
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            value = Path(path).read_text().strip()
            if value.isdigit() and int(value) < (1 << 60):
                return int(value) // (1024 * 1024)
        except OSError:
            pass
    return None


def detect_gpu() -> GPU | None:
    command = ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=True)
        line = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
        name, total, free = [part.strip() for part in line.split(",", 2)]
        return GPU(name=name, total_mb=int(float(total)), free_mb=int(float(free)))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def detect_profile() -> MachineProfile:
    memory = psutil.virtual_memory()
    total = memory.total // (1024 * 1024)
    available = memory.available // (1024 * 1024)
    limit = container_ram_limit_mb()
    usable = min(available, limit) if limit else available
    gpu = detect_gpu()
    return MachineProfile(
        ram_total_mb=total,
        ram_available_mb=available,
        cpu_cores=psutil.cpu_count() or 1,
        gpu=gpu,
        container_ram_limit_mb=limit,
        ram_budget_mb=max(0, int(usable * 0.8)),
        vram_budget_mb=int((gpu.free_mb if gpu else 0) * 0.8),
    )
