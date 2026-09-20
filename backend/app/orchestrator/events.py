import asyncio
from collections import defaultdict, deque
from typing import Any, AsyncIterator


class EventHub:
    def __init__(self, max_events: int = 200):
        self._events: dict[int, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=max_events))
        self._queues: dict[int, set[asyncio.Queue[dict[str, Any] | None]]] = defaultdict(set)
        self._finished: set[int] = set()
        self._lock = asyncio.Lock()

    async def publish(self, job_id: int, event: dict[str, Any]) -> None:
        async with self._lock:
            self._events[job_id].append(event)
            for queue in list(self._queues[job_id]):
                await queue.put(event)

    async def finish(self, job_id: int) -> None:
        async with self._lock:
            self._finished.add(job_id)
            for queue in list(self._queues[job_id]):
                await queue.put(None)

    async def has_events(self, job_id: int) -> bool:
        async with self._lock:
            return bool(self._events[job_id])

    async def subscribe(self, job_id: int, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        async with self._lock:
            cached = [event for event in self._events[job_id] if int(event.get("id", 0)) > after]
            self._queues[job_id].add(queue)
        try:
            for event in cached:
                yield event
            if job_id in self._finished:
                return
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            async with self._lock:
                self._queues[job_id].discard(queue)
