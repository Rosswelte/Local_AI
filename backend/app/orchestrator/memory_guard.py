import asyncio
from typing import Callable

import psutil


class MemoryGuard:
    def __init__(self, threshold_percent: float = 5.0, sample: Callable = psutil.virtual_memory):
        self.threshold_percent = threshold_percent
        self.sample = sample

    def free_percent(self) -> float:
        memory = self.sample()
        return memory.available * 100 / memory.total if memory.total else 0

    def should_stop(self) -> bool:
        return self.free_percent() < self.threshold_percent

    async def watch(self, stop: asyncio.Event, on_low_memory: Callable[[], object]) -> None:
        while not stop.is_set():
            if self.should_stop():
                result = on_low_memory()
                if asyncio.iscoroutine(result):
                    await result
                return
            try:
                await asyncio.wait_for(stop.wait(), 1.0)
            except asyncio.TimeoutError:
                pass
