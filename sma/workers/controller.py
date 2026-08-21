"""WorkerController — bridges Dispatch → Executor → EventBus."""

from __future__ import annotations

import asyncio
from typing import Any

from sma.dispatch import DispatchService
from sma.events import EventBus
from sma.executors.base import Executor, StartRequest
from sma.executors.cursor import CursorExecutor
from sma.executors.opencode import OpenCodeExecutor
from sma.models import ExecutorType, WorkerStatus
from sma.db import Store, utcnow


class ExecutorRegistry:
    def __init__(self) -> None:
        self._map: dict[ExecutorType, Executor] = {
            ExecutorType.OPENCODE: OpenCodeExecutor(),
            ExecutorType.CURSOR: CursorExecutor(),
        }

    def get(self, t: ExecutorType) -> Executor:
        return self._map[t]

    def register(self, t: ExecutorType, executor: Executor) -> None:
        self._map[t] = executor


class WorkerController:
    def __init__(
        self,
        store: Store,
        dispatch: DispatchService,
        events: EventBus,
        registry: ExecutorRegistry | None = None,
    ) -> None:
        self.store = store
        self.dispatch = dispatch
        self.events = events
        self.registry = registry or ExecutorRegistry()
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def run_task(self, task_id: str, repo_path: str) -> str:
        worker = self.dispatch.spawn(task_id)
        task = self.store.get_task(task_id)
        assert task is not None
        executor = self.registry.get(task.executor_type)
        handle = await executor.start(
            StartRequest(
                task_id=task.id,
                worker_id=worker.id,
                repo_path=repo_path,
                prompt=task.prompt,
            )
        )
        worker.executor_run_id = handle.run_id
        worker.pid = handle.pid
        worker.agent_id = handle.agent_id
        worker.run_id = handle.run_id
        worker.session_id = handle.session_id
        worker.status = WorkerStatus.RUNNING
        worker.heartbeat_at = utcnow()
        self.store.save_worker(worker)

        async def _pump() -> None:
            try:
                async for raw in executor.stream(handle):
                    self.dispatch.heartbeat(task.id, worker.id)
                    self.events.publish(
                        task.session_id,
                        raw if isinstance(raw, dict) else {"type": "executor.log", "data": str(raw)},
                        task_id=task.id,
                        worker_id=worker.id,
                    )
                    if isinstance(raw, dict) and raw.get("type") == "worker_failed":
                        w = self.store.get_worker(worker.id)
                        if w:
                            w.status = WorkerStatus.FAILED
                            w.exit_reason = str(raw.get("reason") or "failed")
                            w.heartbeat_at = utcnow()
                            self.store.save_worker(w)
                        return
                w = self.store.get_worker(worker.id)
                if w and w.status == WorkerStatus.RUNNING:
                    w.status = WorkerStatus.COMPLETED
                    w.heartbeat_at = utcnow()
                    self.store.save_worker(w)
            except Exception as e:  # noqa: BLE001
                self.events.publish(
                    task.session_id,
                    {
                        "type": "worker_failed",
                        "reason": "exception",
                        "summary": str(e),
                        "retryable": True,
                        "executor": task.executor_type.value,
                        "worker_id": worker.id,
                        "task_id": task.id,
                    },
                    task_id=task.id,
                    worker_id=worker.id,
                )
                w = self.store.get_worker(worker.id)
                if w:
                    w.status = WorkerStatus.FAILED
                    w.exit_reason = str(e)
                    self.store.save_worker(w)

        self._tasks[worker.id] = asyncio.create_task(_pump())
        return worker.id

    def capabilities(self, executor_type: ExecutorType) -> dict[str, Any]:
        caps = self.registry.get(executor_type).capabilities
        return {
            "cancel": caps.cancel,
            "pause": caps.pause,
            "inject": caps.inject,
            "resume": caps.resume,
            "diff": caps.diff,
        }
