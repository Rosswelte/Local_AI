import time
from typing import Any


async def measure_provider(provider, model: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    tokens = 0
    async for event in provider.run(model, [{"role": "user", "content": "Réponds par une phrase courte."}], {"job_id": -int(model["id"])}):
        if event.type == "token":
            tokens += max(1, len(str(event.data)) // 4)
    elapsed = max(time.perf_counter() - started, 0.001)
    return {"tokens_per_s": round(tokens / elapsed, 2), "tokens": tokens, "duration_s": round(elapsed, 3), "source": "measured"}
