from app.hardware.compatibility import evaluate


def test_same_model_changes_level_by_profile():
    model = {"ram_min_mb": 4000, "vram_min_mb": 0, "gpu_required": False, "perf": {"cpu_tokens_s": 8}}
    small = {"ram_budget_mb": 2000, "vram_budget_mb": 0, "gpu": None}
    large = {"ram_budget_mb": 16000, "vram_budget_mb": 8000, "gpu": {"name": "GPU"}}
    assert evaluate(model, small)["level"] == "impossible"
    assert evaluate(model, large)["level"] == "fast"


def test_impossible_is_never_fast():
    model = {"ram_min_mb": 1000, "gpu_required": True, "vram_min_mb": 9000, "perf": {"cpu_tokens_s": 100}}
    result = evaluate(model, {"ram_budget_mb": 16000, "vram_budget_mb": 1000, "gpu": None})
    assert result["level"] == "impossible"
