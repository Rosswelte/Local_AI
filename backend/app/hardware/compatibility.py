from typing import Any


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def evaluate(model: Any, profile: Any, budgets: dict[str, int] | None = None) -> dict[str, Any]:
    budgets = budgets or {
        "ram_mb": _value(profile, "ram_budget_mb", 0),
        "vram_mb": _value(profile, "vram_budget_mb", 0),
    }
    ram_need = int(_value(model, "ram_min_mb", 0) or 0)
    vram_need = int(_value(model, "vram_min_mb", 0) or 0)
    ram_budget = int(budgets.get("ram_mb", 0))
    vram_budget = int(budgets.get("vram_mb", 0))
    gpu_required = bool(_value(model, "gpu_required", False))
    has_gpu = _value(profile, "gpu", None) is not None
    if ram_need > ram_budget or (gpu_required and (not has_gpu or vram_need > vram_budget)):
        missing = max(ram_need - ram_budget, 0)
        if gpu_required:
            missing = max(missing, vram_need - vram_budget)
        return {"level": "impossible", "source": "estimated", "reason": "Mémoire disponible insuffisante", "missing_mb": missing}
    if ram_need > ram_budget * 0.9 or (vram_need and vram_need > vram_budget * 0.9):
        return {"level": "not_recommended", "source": "estimated", "reason": "Le modèle utilise presque tout le budget", "missing_mb": 0}
    perf = _value(model, "perf", {}) or {}
    cpu_speed = float(perf.get("cpu_tokens_s", 0)) if isinstance(perf, dict) else 0
    level = "fast" if cpu_speed >= 6 else "usable"
    return {"level": level, "source": "estimated", "reason": "Mémoire et vitesse estimées compatibles", "missing_mb": 0}
