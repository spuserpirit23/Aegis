"""Small synchronous event bus used to connect cognitive subsystems.

Subscribers receive a copy of each event and cannot interrupt the publisher.
The bus intentionally has no transport dependency; an application can bridge
these events to logging, a UI, or a message broker at its boundary.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable


class EventBus:
    def __init__(self, history_limit: int = 500) -> None:
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)
        self._events: list[dict[str, Any]] = []
        self._history_limit = max(1, int(history_limit))

    def subscribe(self, topic: str, callback: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers[topic].append(callback)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        event = {"event": topic, **payload}
        self._events.append(event)
        self._events = self._events[-self._history_limit:]
        for callback in self._subscribers[topic] + self._subscribers["*"]:
            try:
                callback(dict(event))
            except Exception:
                # Observability consumers must never break a conversation.
                continue

    def events(self, *, clear: bool = False) -> list[dict[str, Any]]:
        events = [dict(event) for event in self._events]
        if clear:
            self._events.clear()
        return events
