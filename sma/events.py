"""Durable event bus + normalizer."""

from __future__ import annotations

from typing import Any

from sma.db import Store
from sma.redactor import SecretRedactor


class EventNormalizer:
    """Map executor-specific events into SMA event types."""

    def normalize(self, raw: dict[str, Any], *, default_type: str = "executor.event") -> tuple[str, dict[str, Any]]:
        etype = str(raw.get("type") or raw.get("event") or default_type)
        payload = {k: v for k, v in raw.items() if k not in ("type", "event")}
        return etype, payload


class EventBus:
    def __init__(self, store: Store, redactor: SecretRedactor | None = None) -> None:
        self.store = store
        self.redactor = redactor or SecretRedactor()
        self.normalizer = EventNormalizer()

    def publish(
        self,
        session_id: str,
        raw: dict[str, Any],
        *,
        task_id: str | None = None,
        worker_id: str | None = None,
    ) -> int:
        etype, payload = self.normalizer.normalize(raw)
        safe = self.redactor.redact_payload(payload)
        return self.store.append_event(session_id, etype, safe, task_id=task_id, worker_id=worker_id)

    def since(self, session_id: str, last_event_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        return self.store.events_after(session_id, after_id=last_event_id, limit=limit)
