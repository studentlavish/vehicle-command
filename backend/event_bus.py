"""
In-process pub/sub event bus. Any coroutine can subscribe() to receive events;
publishers call publish(event) — non-blocking, drops overflow items on slow
consumers.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

logger = logging.getLogger("rdx.event_bus")


class EventBus:
    def __init__(self, max_queue: int = 200) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self._max_queue = max_queue

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(q)
        logger.info("event_bus: subscriber added (total=%d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)
        logger.info("event_bus: subscriber removed (total=%d)", len(self._subscribers))

    def publish(self, event: Dict[str, Any]) -> None:
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("event_bus: subscriber queue full — dropping event")
            except Exception:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)


event_bus = EventBus()
