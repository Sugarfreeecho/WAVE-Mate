from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import DefaultDict, Dict, Set

from .event_schema import RuntimeEvent


class StreamPublisher:
    def __init__(self):
        self._subscribers: DefaultDict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers[session_id].add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            queues = self._subscribers.get(session_id)
            if not queues:
                return
            queues.discard(queue)
            if not queues:
                self._subscribers.pop(session_id, None)

    async def publish(self, event: RuntimeEvent) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(event.session_id, set()))
        for queue in queues:
            await queue.put(event)

    async def publish_many(self, events: list[RuntimeEvent]) -> None:
        for event in events:
            await self.publish(event)

    async def stats(self) -> Dict[str, int]:
        async with self._lock:
            return {session_id: len(queues) for session_id, queues in self._subscribers.items()}
