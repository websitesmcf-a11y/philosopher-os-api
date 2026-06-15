"""Shared SSE event bus.

A single in-process pub/sub bus used to broadcast live execution events
(task runs, agent runs, etc.) to any number of subscribers — primarily the
SSE stream consumed by the dashboard's Agent Run Logs page.

Both the task executor and the chat router publish to this bus so that
scheduled task executions *and* interactive/Beast Mode agent runs surface
in the same live log stream.
"""
import asyncio
import json
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# ─── Subscriber registry ────────────────────────────────────────
_event_listeners: list[asyncio.Queue] = []


def broadcast(event: dict) -> None:
    """Publish an event to every active subscriber.

    Never raises — a slow/full subscriber queue must not break the caller
    (e.g. an agent run). Full queues simply drop the event for that listener.
    """
    for q in list(_event_listeners):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("event_bus: subscriber queue full, dropping event")
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("event_bus: failed to deliver event: %s", e)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _event_listeners.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    if q in _event_listeners:
        _event_listeners.remove(q)


async def event_stream() -> AsyncGenerator[str, None]:
    """SSE generator for live execution events."""
    q = subscribe()
    try:
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
    finally:
        unsubscribe(q)
